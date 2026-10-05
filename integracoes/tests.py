import json
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from atendimento.models import Conversa, Mensagem, Paciente
from atendimento.services import robo_pode_responder
from core.models import Clinica, RegistroLog, Unidade

from . import google
from .evolution import ErroEvolution, EvolutionClient, atualizar_status
from .models import Configuracao, ContaGoogle, InstanciaWhatsApp, StatusConexao

TOKEN = "token-teste"


def payload_mensagem(texto="Oi, quero marcar uma consulta", from_me=False, msg_id="ABC1", jid="5581999990000@s.whatsapp.net"):
    return {
        "event": "messages.upsert",
        "instance": "clinica-recife",
        "data": {
            "key": {"remoteJid": jid, "fromMe": from_me, "id": msg_id},
            "pushName": "Maria",
            "message": {"conversation": texto},
            "messageType": "conversation",
        },
    }


@override_settings(WEBHOOK_TOKEN=TOKEN)
class WebhookEvolutionTests(TestCase):
    def setUp(self):
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Recife")
        self.instancia = InstanciaWhatsApp.objects.create(unidade=self.unidade, nome_instancia="clinica-recife")
        self.url = reverse("integracoes:webhook_evolution") + f"?token={TOKEN}"
        # Estes testes cobrem só o webhook; o assistente tem testes próprios (atendimento/tests.py).
        patcher = mock.patch("integracoes.views.disparar_assistente")
        self.disparar = patcher.start()
        self.addCleanup(patcher.stop)

    def enviar(self, corpo, url=None):
        return self.client.post(url or self.url, json.dumps(corpo), content_type="application/json")

    def test_token_invalido_e_recusado(self):
        resp = self.enviar(payload_mensagem(), url=reverse("integracoes:webhook_evolution") + "?token=errado")
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Mensagem.objects.exists())

    def test_mensagem_do_paciente_cria_paciente_conversa_e_mensagem(self):
        self.assertEqual(self.enviar(payload_mensagem()).status_code, 200)
        paciente = Paciente.objects.get()
        self.assertEqual((paciente.telefone, paciente.nome), ("5581999990000", "Maria"))
        msg = Mensagem.objects.get()
        self.assertEqual(msg.origem, Mensagem.Origem.PACIENTE)
        self.assertTrue(robo_pode_responder(msg.conversa))

    def test_mensagem_duplicada_e_ignorada(self):
        self.enviar(payload_mensagem())
        self.enviar(payload_mensagem())
        self.assertEqual(Mensagem.objects.count(), 1)

    def test_resposta_pelo_celular_pausa_o_robo(self):
        self.enviar(payload_mensagem())
        self.enviar(payload_mensagem(texto="Oi Maria, aqui é a secretária", from_me=True, msg_id="ABC2"))
        conversa = Conversa.objects.get()
        self.assertEqual(conversa.mensagens.last().origem, Mensagem.Origem.HUMANO)
        self.assertGreater(conversa.robo_pausado_ate, timezone.now())
        self.assertFalse(robo_pode_responder(conversa))

    def test_grupos_sao_ignorados(self):
        self.enviar(payload_mensagem(jid="1234@g.us"))
        self.assertFalse(Mensagem.objects.exists())

    def test_connection_update_atualiza_status(self):
        self.enviar({"event": "connection.update", "instance": "clinica-recife", "data": {"state": "open"}})
        self.instancia.refresh_from_db()
        self.assertEqual(self.instancia.status, StatusConexao.CONECTADO)

    def test_rajada_de_eventos_iguais_gera_um_log_so(self):
        evento = {"event": "connection.update", "instance": "clinica-recife", "data": {"state": "connecting"}}
        for _ in range(5):
            self.enviar(evento)
        self.enviar({**evento, "data": {"state": "open"}})
        logs = RegistroLog.objects.filter(origem="clinica.webhook", mensagem__contains="mudou para")
        self.assertEqual(logs.count(), 2)  # nao_configurado→connecting, connecting→open


class CriptografiaTests(TestCase):
    def test_segredo_fica_criptografado_no_banco(self):
        config = Configuracao.obter()
        config.evolution_api_key = "minha-chave-secreta"
        config.save()
        with connection.cursor() as c:
            c.execute("SELECT evolution_api_key FROM integracoes_configuracao WHERE id = 1")
            bruto = c.fetchone()[0]
        self.assertNotIn("minha-chave-secreta", bruto)
        self.assertEqual(Configuracao.obter().evolution_api_key, "minha-chave-secreta")


class EvolutionClientTests(TestCase):
    def setUp(self):
        config = Configuracao.obter()
        config.evolution_url = "http://evolution:8080/"
        config.evolution_api_key = "k"
        config.save()
        clinica = Clinica.objects.create(nome="Teste")
        self.instancia = InstanciaWhatsApp.objects.create(
            unidade=Unidade.objects.create(clinica=clinica, nome="Recife"), nome_instancia="clinica-recife"
        )

    @mock.patch("integracoes.evolution.requests.request")
    def test_status_conectado(self, req):
        req.return_value = mock.Mock(status_code=200, content=b"x", json=lambda: {"instance": {"state": "open"}})
        self.assertEqual(atualizar_status(self.instancia), StatusConexao.CONECTADO)
        self.assertEqual(req.call_args.args[1], "http://evolution:8080/instance/connectionState/clinica-recife")
        self.assertEqual(req.call_args.kwargs["headers"]["apikey"], "k")

    @mock.patch("integracoes.evolution.requests.request")
    def test_falha_gera_status_erro_e_log(self, req):
        req.return_value = mock.Mock(status_code=401, text="Unauthorized", content=b"x")
        self.assertEqual(atualizar_status(self.instancia), StatusConexao.ERRO)
        self.assertTrue(RegistroLog.objects.filter(nivel="ERROR", origem="clinica.evolution").exists())

    def test_sem_configuracao_lanca_erro(self):
        config = Configuracao.obter()
        config.evolution_url = ""
        config.save()
        with self.assertRaisesMessage(Exception, "não configuradas"):
            EvolutionClient()


class PainelTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")

    def test_paginas_exigem_login(self):
        resp = self.client.get(reverse("painel:dashboard"))
        self.assertRedirects(resp, reverse("login") + "?next=/")

    def test_paginas_abrem_para_superusuario(self):
        self.client.force_login(self.admin)
        for nome in ("painel:dashboard", "painel:integracoes", "painel:logs"):
            with self.subTest(nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_usuario_comum_e_bloqueado(self):
        self.client.force_login(User.objects.create_user("comum", password="senha-forte-123"))
        self.assertEqual(self.client.get(reverse("painel:integracoes")).status_code, 403)

    def test_salvar_evolution_mantem_chave_se_campo_vazio(self):
        self.client.force_login(self.admin)
        url = reverse("painel:integracoes")
        self.client.post(url, {"formulario": "evolution", "evolution-evolution_url": "http://x:8080", "evolution-evolution_api_key": "abc"})
        self.client.post(url, {"formulario": "evolution", "evolution-evolution_url": "http://y:8080", "evolution-evolution_api_key": ""})
        config = Configuracao.obter()
        self.assertEqual((config.evolution_url, config.evolution_api_key), ("http://y:8080", "abc"))


def resposta(status=200, corpo=None):
    r = mock.Mock(status_code=status, ok=status < 400)
    r.content = b"x"
    r.json.return_value = corpo or {}
    r.text = json.dumps(corpo or {})
    return r


ESCOPO_OK = "openid https://www.googleapis.com/auth/calendar https://www.googleapis.com/auth/userinfo.email"


@override_settings(URL_SITE="http://localhost:8000")
class GoogleOAuthTests(TestCase):
    def setUp(self):
        config = Configuracao.obter()
        config.google_client_id = "cliente-123.apps.googleusercontent.com"
        config.google_client_secret = "segredo"
        config.save()
        self.admin = User.objects.create_superuser("admin", "a@a.com", "senha-forte-123")
        self.client.force_login(self.admin)
        self.unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")

    def iniciar(self):
        """Clica em Conectar e devolve o state que foi para o Google."""
        resp = self.client.post(reverse("painel:google_conectar", args=[self.unidade.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp["Location"].startswith(google.URL_AUTORIZACAO))
        from urllib.parse import parse_qs, urlparse
        params = parse_qs(urlparse(resp["Location"]).query)
        self.assertEqual(params["redirect_uri"], ["http://localhost:8000/integracoes/google/callback/"])
        self.assertEqual(params["access_type"], ["offline"])
        return params["state"][0]

    @mock.patch("integracoes.google.requests.get")
    @mock.patch("integracoes.google.requests.post")
    def test_fluxo_completo_salva_token_criptografado_e_email(self, post, get):
        post.return_value = resposta(200, {"access_token": "at", "refresh_token": "rt", "expires_in": 3599, "scope": ESCOPO_OK})
        get.return_value = resposta(200, {"email": "clinica@exemplo.com"})
        state = self.iniciar()

        resp = self.client.get(reverse("painel:google_callback"), {"state": state, "code": "codigo-do-google"})

        self.assertRedirects(resp, reverse("painel:google_unidade", args=[self.unidade.pk]), fetch_redirect_response=False)
        conta = ContaGoogle.objects.get(unidade=self.unidade)
        self.assertEqual((conta.email, conta.status), ("clinica@exemplo.com", StatusConexao.CONECTADO))
        self.assertEqual(json.loads(conta.token_json)["refresh_token"], "rt")
        with connection.cursor() as c:
            c.execute("SELECT token_json FROM integracoes_contagoogle WHERE id = %s", [conta.pk])
            bruto = c.fetchone()[0]
        self.assertNotIn("refresh_token", bruto)
        self.assertNotIn("access_token", bruto)
        self.assertEqual(post.call_args.kwargs["data"]["code"], "codigo-do-google")

    @mock.patch("integracoes.google.requests.post")
    def test_state_adulterado_e_recusado(self, post):
        self.iniciar()
        resp = self.client.get(reverse("painel:google_callback"), {"state": "forjado", "code": "x"})
        self.assertRedirects(resp, reverse("painel:integracoes"), fetch_redirect_response=False)
        post.assert_not_called()
        self.assertFalse(ContaGoogle.objects.filter(unidade=self.unidade).exists())

    @mock.patch("integracoes.google.requests.post")
    def test_state_de_outra_sessao_e_recusado(self, post):
        state = self.iniciar()
        outra_sessao = self.client_class()
        outra_sessao.force_login(self.admin)
        resp = outra_sessao.get(reverse("painel:google_callback"), {"state": state, "code": "x"})
        self.assertRedirects(resp, reverse("painel:integracoes"), fetch_redirect_response=False)
        post.assert_not_called()

    @mock.patch("integracoes.google.requests.post")
    def test_cancelamento_no_google_nao_cria_conta(self, post):
        state = self.iniciar()
        self.client.get(reverse("painel:google_callback"), {"state": state, "error": "access_denied"})
        post.assert_not_called()
        self.assertFalse(ContaGoogle.objects.filter(unidade=self.unidade).exists())

    @mock.patch("integracoes.google.requests.get")
    @mock.patch("integracoes.google.requests.post")
    def test_sem_permissao_de_agenda_e_recusado(self, post, get):
        post.return_value = resposta(200, {"access_token": "at", "refresh_token": "rt", "scope": "openid email"})
        state = self.iniciar()
        self.client.get(reverse("painel:google_callback"), {"state": state, "code": "x"})
        self.assertFalse(ContaGoogle.objects.filter(unidade=self.unidade).exists())

    def test_conectar_sem_credenciais_avisa(self):
        config = Configuracao.obter()
        config.google_client_id = ""
        config.save()
        resp = self.client.post(reverse("painel:google_conectar", args=[self.unidade.pk]))
        self.assertRedirects(resp, reverse("painel:integracoes"), fetch_redirect_response=False)


class GoogleAgendaTests(TestCase):
    def setUp(self):
        config = Configuracao.obter()
        config.google_client_id = "id"
        config.google_client_secret = "segredo"
        config.save()
        unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")
        self.conta = ContaGoogle.objects.create(unidade=unidade)

    def com_token(self, expira_em):
        self.conta.token_json = json.dumps({"access_token": "velho", "refresh_token": "rt", "expira_em": expira_em})
        self.conta.save()

    @mock.patch("integracoes.google.requests.request")
    @mock.patch("integracoes.google.requests.post")
    def test_token_vencido_e_renovado_e_salvo(self, post, req):
        self.com_token(expira_em=0)
        post.return_value = resposta(200, {"access_token": "novo", "expires_in": 3599})
        req.return_value = resposta(200, {"items": []})

        google.GoogleAgenda(self.conta).listar_calendarios()

        self.assertEqual(req.call_args.kwargs["headers"]["Authorization"], "Bearer novo")
        self.conta.refresh_from_db()
        token = json.loads(self.conta.token_json)
        self.assertEqual((token["access_token"], token["refresh_token"]), ("novo", "rt"))

    @mock.patch("integracoes.google.requests.request")
    @mock.patch("integracoes.google.requests.post")
    def test_token_valido_nao_renova(self, post, req):
        import time
        self.com_token(expira_em=int(time.time()) + 3000)
        req.return_value = resposta(200, {"items": []})
        google.GoogleAgenda(self.conta).listar_calendarios()
        post.assert_not_called()

    @mock.patch("integracoes.google.requests.post")
    def test_refresh_revogado_marca_desconectado_e_loga(self, post):
        self.com_token(expira_em=0)
        post.return_value = resposta(400, {"error": "invalid_grant"})
        self.assertEqual(google.atualizar_status(self.conta), StatusConexao.DESCONECTADO)
        self.assertIn("novamente", self.conta.detalhe_status)
        self.assertTrue(RegistroLog.objects.filter(origem="clinica.google", nivel="ERROR").exists())

    @mock.patch("integracoes.google.requests.request")
    def test_listar_calendarios_principal_primeiro(self, req):
        import time
        self.com_token(expira_em=int(time.time()) + 3000)
        req.return_value = resposta(200, {"items": [
            {"id": "b", "summary": "Dra. Bia"},
            {"id": "me@x.com", "summary": "me@x.com", "primary": True},
            {"id": "a", "summary": "Dr. Alan", "summaryOverride": "Alan (consultório)"},
        ]})
        nomes = [c["nome"] for c in google.GoogleAgenda(self.conta).listar_calendarios()]
        self.assertEqual(nomes, ["me@x.com", "Alan (consultório)", "Dra. Bia"])

    @mock.patch("integracoes.google.requests.request")
    def test_ocupados_converte_intervalos(self, req):
        import time
        from datetime import datetime
        self.com_token(expira_em=int(time.time()) + 3000)
        req.return_value = resposta(200, {"calendars": {"cal": {"busy": [
            {"start": "2026-10-05T12:00:00Z", "end": "2026-10-05T13:00:00Z"},
        ]}}})
        inicio = timezone.make_aware(datetime(2026, 10, 5))
        ocupados = google.GoogleAgenda(self.conta).ocupados(["cal"], inicio, inicio)
        (ini, fim), = ocupados["cal"]
        self.assertEqual(timezone.localtime(ini).hour, 9)  # 12h UTC = 9h em Recife
        self.assertEqual((fim - ini).seconds, 3600)

    @mock.patch("integracoes.google.requests.request")
    def test_erro_de_calendario_no_freebusy_vira_excecao(self, req):
        import time
        from datetime import datetime
        self.com_token(expira_em=int(time.time()) + 3000)
        req.return_value = resposta(200, {"calendars": {"cal": {"errors": [{"reason": "notFound"}]}}})
        inicio = timezone.make_aware(datetime(2026, 10, 5))
        with self.assertRaisesMessage(google.ErroGoogle, "notFound"):
            google.GoogleAgenda(self.conta).ocupados(["cal"], inicio, inicio)

class GoogleUnidadePaginaTests(TestCase):
    def setUp(self):
        from core.models import Dentista, HorarioAtendimento, Procedimento
        from datetime import time
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        ContaGoogle.objects.create(unidade=self.unidade, token_json='{"access_token": "x"}', status=StatusConexao.CONECTADO)
        self.sem_horario = Dentista.objects.create(unidade=self.unidade, nome="Vinicius")
        self.com_horario = Dentista.objects.create(unidade=self.unidade, nome="Dra. Ana")
        HorarioAtendimento.objects.create(dentista=self.com_horario, dia_semana=0, inicio=time(8), fim=time(12))
        self.proc = Procedimento.objects.create(clinica=clinica, nome="Avaliação", duracao_minutos=30)
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))
        self.url = reverse("painel:google_unidade", args=[self.unidade.pk])

    @mock.patch("painel.views.buscar_horarios_livres")
    @mock.patch("integracoes.google.GoogleAgenda.listar_calendarios", return_value=[{"id": "p", "nome": "p", "principal": True}])
    def test_dentista_sem_horario_avisa_e_nao_consulta_google(self, _cal, buscar):
        resp = self.client.get(self.url, {"dentista": self.sem_horario.pk, "procedimento": self.proc.pk})
        self.assertContains(resp, "Vinicius não tem horário de atendimento")
        buscar.assert_not_called()

    @mock.patch("painel.views.buscar_horarios_livres", return_value={})
    @mock.patch("integracoes.google.GoogleAgenda.listar_calendarios", return_value=[{"id": "p", "nome": "p", "principal": True}])
    def test_dentista_com_horario_consulta_normalmente(self, _cal, buscar):
        resp = self.client.get(self.url, {"dentista": self.com_horario.pk, "procedimento": self.proc.pk})
        self.assertNotContains(resp, "não tem horário de atendimento")
        self.assertContains(resp, "Nenhum horário livre")
        buscar.assert_called_once()

class ConsultaMarcarViewTests(TestCase):
    def setUp(self):
        from core.models import Dentista, Procedimento
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        self.dentista = Dentista.objects.create(unidade=self.unidade, nome="Dra. Ana")
        self.proc = Procedimento.objects.create(clinica=clinica, nome="Avaliação", duracao_minutos=30)
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))
        self.url = reverse("painel:consulta_marcar", args=[self.unidade.pk])

    def dados(self, **extra):
        return {"dentista": self.dentista.pk, "procedimento": self.proc.pk, "inicio": "2026-10-05T09:00:00-03:00",
                "nome": "Maria", "telefone": "(31) 99999-0000", **extra}

    @mock.patch("atendimento.agendamentos.marcar")
    def test_marca_e_cria_paciente_com_telefone_normalizado(self, marcar):
        marcar.return_value = mock.Mock(inicio=timezone.now())
        resp = self.client.post(self.url, self.dados())
        self.assertEqual(resp.status_code, 302)
        paciente = marcar.call_args.args[0]
        self.assertEqual((paciente.nome, paciente.telefone), ("Maria", "5531999990000"))
        self.assertEqual(marcar.call_args.args[3].isoformat(), "2026-10-05T09:00:00-03:00")

    @mock.patch("atendimento.agendamentos.marcar")
    def test_telefone_invalido_nao_marca(self, marcar):
        self.client.post(self.url, self.dados(telefone="123"))
        marcar.assert_not_called()
        self.assertFalse(Paciente.objects.exists())

    @mock.patch("atendimento.agendamentos.marcar")
    def test_volta_so_para_pagina_do_proprio_site(self, marcar):
        resp = self.client.post(self.url, self.dados(telefone="123"), HTTP_REFERER="https://site-malicioso.com/x")
        self.assertNotIn("malicioso", resp["Location"])

@override_settings(WEBHOOK_TOKEN=TOKEN)
class CorrecoesWhatsAppTests(TestCase):
    """pushName nulo, desconectar tolerante e troca de número (01/10/2026)."""

    def setUp(self):
        config = Configuracao.obter()
        config.evolution_url = "http://evolution:8080"
        config.evolution_api_key = "k"
        config.save()
        self.unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Minas")
        self.instancia = InstanciaWhatsApp.objects.create(
            unidade=self.unidade, nome_instancia="clinica-minas-2", numero="5531988880000", status=StatusConexao.CONECTADO
        )
        patcher = mock.patch("integracoes.views.disparar_assistente")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))

    def test_push_name_nulo_nao_perde_a_mensagem(self):
        corpo = payload_mensagem(msg_id="N1")
        corpo["instance"] = "clinica-minas-2"
        corpo["data"]["pushName"] = None
        self.client.post(reverse("integracoes:webhook_evolution") + f"?token={TOKEN}", json.dumps(corpo), content_type="application/json")
        self.assertEqual(Mensagem.objects.count(), 1)
        self.assertEqual(Paciente.objects.get().nome, "")
        self.assertFalse(RegistroLog.objects.filter(nivel="ERROR").exists())

    @mock.patch("painel.views.atualizar_status")
    @mock.patch("integracoes.evolution.EvolutionClient.desconectar")
    def test_desconectar_ok(self, desconectar, atualizar):
        atualizar.return_value = StatusConexao.DESCONECTADO
        resp = self.client.post(reverse("painel:whatsapp_desconectar", args=[self.unidade.pk]))
        desconectar.assert_called_once_with("clinica-minas-2")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(RegistroLog.objects.filter(mensagem__contains="desconectado por").exists())

    @mock.patch("painel.views.atualizar_status", return_value=StatusConexao.DESCONECTADO)
    @mock.patch("integracoes.evolution.EvolutionClient.desconectar", side_effect=ErroEvolution("400 já desconectado"))
    def test_desconectar_ja_desconectado_conta_como_sucesso(self, _d, _a):
        self.client.post(reverse("painel:whatsapp_desconectar", args=[self.unidade.pk]))
        self.assertFalse(RegistroLog.objects.filter(nivel="ERROR").exists())

    @mock.patch("painel.views.atualizar_status", return_value=StatusConexao.CONECTADO)
    @mock.patch("integracoes.evolution.EvolutionClient.desconectar", side_effect=ErroEvolution("500"))
    def test_desconectar_que_falha_de_verdade_avisa(self, _d, _a):
        self.client.post(reverse("painel:whatsapp_desconectar", args=[self.unidade.pk]))
        self.assertTrue(RegistroLog.objects.filter(nivel="ERROR", mensagem__contains="Falha ao desconectar").exists())

    @mock.patch("integracoes.evolution.EvolutionClient.configurar_webhook")
    @mock.patch("integracoes.evolution.EvolutionClient.instancia_existe", return_value=True)
    def test_trocar_numero_normaliza_e_grava(self, _existe, _webhook):
        self.client.post(reverse("painel:whatsapp_conectar", args=[self.unidade.pk]), {"numero": "(31) 97777-1111"})
        self.instancia.refresh_from_db()
        self.assertEqual(self.instancia.numero, "5531977771111")
        self.assertTrue(RegistroLog.objects.filter(mensagem__contains="trocado de +5531988880000 para +5531977771111").exists())

    @mock.patch("integracoes.evolution.EvolutionClient.instancia_existe")
    def test_numero_invalido_nao_conecta(self, existe):
        self.client.post(reverse("painel:whatsapp_conectar", args=[self.unidade.pk]), {"numero": "123"})
        existe.assert_not_called()
        self.instancia.refresh_from_db()
        self.assertEqual(self.instancia.numero, "5531988880000")

    @mock.patch("integracoes.evolution.requests.request")
    def test_qrcode_pede_codigo_para_o_numero_atual(self, req):
        req.return_value = mock.Mock(status_code=200, content=b"x", json=lambda: {"instance": {"state": "close"}, "base64": "data:x"})
        self.client.get(reverse("painel:whatsapp_qrcode", args=[self.unidade.pk]))
        chamada_connect = [c for c in req.call_args_list if "/instance/connect/" in c.args[1]][0]
        self.assertEqual(chamada_connect.kwargs["params"], {"number": "5531988880000"})

class QrCodeTentativasTests(TestCase):
    def setUp(self):
        config = Configuracao.obter()
        config.evolution_url = "http://evolution:8080"
        config.evolution_api_key = "k"
        config.save()
        unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")
        InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="clinica-recife-1")
        self.url = reverse("painel:whatsapp_qrcode", args=[unidade.pk])
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))

    @mock.patch("painel.views.time.sleep")
    @mock.patch("integracoes.evolution.EvolutionClient.estado_conexao", return_value="connecting")
    @mock.patch("integracoes.evolution.EvolutionClient.obter_qrcode")
    def test_tenta_de_novo_ate_o_qr_ficar_pronto(self, obter, _estado, dormir):
        obter.side_effect = [{"count": 0}, {"base64": "data:image/png;base64,QR"}]
        resp = self.client.get(self.url)
        self.assertEqual(obter.call_count, 2)
        self.assertContains(resp, "data:image/png;base64,QR")
        self.assertNotContains(resp, "Gerando o QR code")

    @mock.patch("painel.views.time.sleep")
    @mock.patch("integracoes.evolution.EvolutionClient.estado_conexao", return_value="connecting")
    @mock.patch("integracoes.evolution.EvolutionClient.obter_qrcode", return_value={"count": 0})
    def test_sem_qr_mostra_gerando_e_nao_mostra_sucesso_visivel(self, obter, _estado, _dormir):
        resp = self.client.get(self.url)
        self.assertEqual(obter.call_count, 4)
        self.assertContains(resp, "Gerando o QR code")
        # O ícone de sucesso existe na página, mas escondido até conectar.
        self.assertContains(resp, 'id="sucesso" class="success-mark" hidden')

@override_settings(WEBHOOK_TOKEN=TOKEN)
class WebhookDisparaAssistenteTests(TestCase):
    def setUp(self):
        unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")
        InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="clinica-recife")
        self.url = reverse("integracoes:webhook_evolution") + f"?token={TOKEN}"

    @mock.patch("integracoes.views.disparar_assistente")
    def test_so_mensagem_do_paciente_aciona(self, disparar):
        self.client.post(self.url, json.dumps(payload_mensagem(msg_id="P1")), content_type="application/json")
        self.client.post(self.url, json.dumps(payload_mensagem(msg_id="E1", from_me=True, texto="equipe")), content_type="application/json")
        self.client.post(self.url, json.dumps(payload_mensagem(msg_id="P1")), content_type="application/json")  # duplicada
        self.assertEqual(disparar.call_count, 1)
        self.assertEqual(disparar.call_args.args[1].texto, "Oi, quero marcar uma consulta")


class SimuladorTests(TestCase):
    def setUp(self):
        config = Configuracao.obter()
        config.ia_api_key = "sk-teste"
        config.save()
        self.unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Minas")
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))

    def test_pagina_abre(self):
        resp = self.client.get(reverse("painel:assistente"))
        self.assertContains(resp, "Simulador")
        self.assertContains(resp, "vão de verdade para o Google Agenda")

    @mock.patch("atendimento.assistente.responder")
    def test_conversa_simulada_nao_usa_whatsapp(self, responder):
        from atendimento.assistente import Resultado
        responder.return_value = Resultado(texto="Olá! Posso ajudar?", acoes=["buscou horários"])
        with mock.patch("integracoes.evolution.EvolutionClient") as evo:
            resp = self.client.post(reverse("painel:assistente_simular"), {"unidade": self.unidade.pk, "texto": "oi"})
            evo.assert_not_called()
        dados = resp.json()
        self.assertEqual([m["origem"] for m in dados["mensagens"]], ["paciente", "robo"])
        self.assertEqual(dados["acoes"], ["buscou horários"])
        conversa = responder.call_args.args[0]
        self.assertTrue(conversa.simulada)

    @mock.patch("atendimento.assistente.responder")
    def test_simulador_nao_mistura_com_conversa_real(self, responder):
        from atendimento.assistente import Resultado
        from atendimento.services import obter_conversa
        responder.return_value = Resultado(texto="Oi")
        self.client.post(reverse("painel:assistente_simular"), {"unidade": self.unidade.pk, "texto": "oi"})
        real = obter_conversa(self.unidade, "5531999990000")
        self.assertFalse(real.simulada)

class FormIAProvedorTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))

    def salvar(self, **campos):
        dados = {"formulario": "ia", "ia-ia_provedor": "gemini", "ia-ia_api_key": "chave", "ia-ia_modelo": "", "ia-ia_base_url": ""}
        dados.update({f"ia-{k}": v for k, v in campos.items()})
        with mock.patch("painel.views.verificar_ia"):
            return self.client.post(reverse("painel:integracoes"), dados)

    def test_trocar_para_gemini_com_modelo_claude_usa_padrao_do_gemini(self):
        self.salvar(ia_modelo="claude-opus-5-5")
        config = Configuracao.obter()
        self.assertEqual((config.ia_provedor, config.ia_modelo), ("gemini", "gemini-3.8-flash"))

    def test_voltar_para_anthropic_usa_padrao_do_claude(self):
        self.salvar(ia_modelo="gemini-3.8-flash", ia_provedor="anthropic")
        self.assertEqual(Configuracao.obter().ia_modelo, "claude-opus-5-5")

    def test_modelo_escolhido_e_respeitado(self):
        self.salvar(ia_modelo="gemini-3.5-flash-lite")
        self.assertEqual(Configuracao.obter().ia_modelo, "gemini-3.5-flash-lite")

    def test_compativel_exige_url(self):
        self.salvar(ia_provedor="compativel", ia_modelo="openai/gpt-oss-120b")
        self.assertNotEqual(Configuracao.obter().ia_provedor, "compativel")


class VerificarIAGeminiTests(TestCase):
    @mock.patch("integracoes.ia.cliente_compativel")
    def test_gemini_valida_chave_e_modelo(self, cliente):
        from .ia import verificar_ia
        cliente.return_value.models.list.return_value = [mock.Mock(id="models/gemini-3.8-flash")]
        config = Configuracao.obter()
        config.ia_provedor, config.ia_modelo, config.ia_api_key = "gemini", "gemini-3.8-flash", "chave"
        config.save()
        self.assertEqual(verificar_ia(), StatusConexao.CONECTADO)
        self.assertIn("Google Gemini", Configuracao.obter().ia_detalhe_status)

    def test_url_do_gemini_e_a_oficial(self):
        from .ia import GEMINI_BASE_URL, base_url
        config = Configuracao.obter()
        config.ia_provedor = "gemini"
        self.assertEqual(base_url(config), GEMINI_BASE_URL)
        self.assertEqual(GEMINI_BASE_URL, "https://generativelanguage.googleapis.com/v1beta/openai/")

class CaixaDeEntradaTests(TestCase):
    def setUp(self):
        from atendimento.models import Conversa
        self.Conversa = Conversa
        self.admin = User.objects.create_superuser("julia", "j@a.com", "senha-forte-123")
        self.client.force_login(self.admin)
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        InstanciaWhatsApp.objects.create(unidade=self.unidade, nome_instancia="clinica-minas-2")
        self.paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000", nome="Maria")
        self.conversa = Conversa.objects.create(paciente=self.paciente, unidade=self.unidade)
        Mensagem.objects.create(conversa=self.conversa, origem="paciente", texto="Quero falar com alguém")

    def test_resumo_da_ultima_mensagem_e_nota_nao_muda(self):
        from atendimento.services import registrar_nota
        self.conversa.refresh_from_db()
        self.assertEqual((self.conversa.ultima_mensagem_origem, self.conversa.ultima_mensagem_texto),
                         ("paciente", "Quero falar com alguém"))
        registrar_nota(self.conversa, "ligar à tarde", self.admin)
        self.conversa.refresh_from_db()
        self.assertEqual(self.conversa.ultima_mensagem_origem, "paciente")

    def test_nota_nao_vai_para_a_ia(self):
        from atendimento.assistente import montar_historico
        from atendimento.services import registrar_nota
        registrar_nota(self.conversa, "paciente difícil, cuidado", self.admin)
        self.assertNotIn("difícil", json.dumps(montar_historico(self.conversa), ensure_ascii=False))

    def test_contador_do_menu_so_conta_quem_espera_a_equipe(self):
        self.conversa.status = "humano"
        self.conversa.save()
        simulada = self.Conversa.objects.create(paciente=self.paciente, unidade=self.unidade, simulada=True, status="humano")
        Mensagem.objects.create(conversa=simulada, origem="paciente", texto="oi")
        resp = self.client.get(reverse("painel:dashboard"))
        self.assertEqual(resp.context["conversas_aguardando"], 1)
        self.assertContains(resp, 'class="nav__badge"')

    def test_lista_filtra_e_esconde_simuladas(self):
        self.Conversa.objects.create(paciente=self.paciente, unidade=self.unidade, simulada=True)
        resp = self.client.get(reverse("painel:conversas"), {"filtro": "todas"})
        self.assertEqual([c.pk for c in resp.context["lista"]], [self.conversa.pk])
        resp = self.client.get(reverse("painel:conversas"), {"filtro": "equipe"})
        self.assertEqual(resp.context["lista"], [])
        resp = self.client.get(reverse("painel:conversas"), {"filtro": "todas", "q": "99999"})
        self.assertEqual(len(resp.context["lista"]), 1)
        resp = self.client.get(reverse("painel:conversas"), {"filtro": "todas", "q": "joão"})
        self.assertEqual(resp.context["lista"], [])

    def test_abrir_conversa(self):
        resp = self.client.get(reverse("painel:conversas"), {"c": self.conversa.pk})
        self.assertEqual(resp.context["selecionada"], self.conversa)
        self.assertContains(resp, "Quero falar com alguém")  # vai no json_script

    @mock.patch("integracoes.evolution.EvolutionClient")
    def test_responder_pelo_painel_assume_e_envia(self, evo):
        evo.return_value.enviar_texto.return_value = {"key": {"id": "ID-EQ"}}
        resp = self.client.post(reverse("painel:conversa_responder", args=[self.conversa.pk]), {"texto": "Oi Maria, aqui é a Júlia"})
        self.assertEqual(resp.status_code, 200)
        evo.return_value.enviar_texto.assert_called_once_with("clinica-minas-2", "5531999990000", "Oi Maria, aqui é a Júlia")
        self.conversa.refresh_from_db()
        self.assertEqual((self.conversa.status, self.conversa.atendente), ("humano", self.admin))
        msg = self.conversa.mensagens.last()
        self.assertEqual((msg.origem, msg.autor, msg.id_externo), ("humano", self.admin, "ID-EQ"))
        self.assertEqual(resp.json()["mensagens"][-1]["autor"], "julia")

    @mock.patch("integracoes.evolution.EvolutionClient")
    def test_eco_da_mensagem_da_equipe_nao_duplica(self, evo):
        from atendimento.services import registrar_mensagem

        def enviar_e_ecoar(instancia, numero, texto):
            registrar_mensagem(self.unidade, numero, texto, "ID-ECO", enviada_por_nos=True)
            return {"key": {"id": "ID-ECO"}}

        evo.return_value.enviar_texto.side_effect = enviar_e_ecoar
        self.client.post(reverse("painel:conversa_responder", args=[self.conversa.pk]), {"texto": "Já vou ver"})
        self.assertEqual(self.conversa.mensagens.filter(texto="Já vou ver").count(), 1)

    @mock.patch("integracoes.evolution.EvolutionClient")
    def test_falha_no_envio_avisa_e_marca_nao_enviada(self, evo):
        from integracoes.evolution import ErroEvolution
        evo.return_value.enviar_texto.side_effect = ErroEvolution("desconectado")
        resp = self.client.post(reverse("painel:conversa_responder", args=[self.conversa.pk]), {"texto": "oi"})
        self.assertEqual(resp.status_code, 502)
        self.assertIn("não foi enviada", resp.json()["erro"])
        self.assertTrue(resp.json()["mensagens"][-1]["falhou"])

    def test_nota_interna(self):
        resp = self.client.post(reverse("painel:conversa_nota", args=[self.conversa.pk]), {"texto": "retornar amanhã"})
        self.assertEqual(resp.json()["mensagens"][-1]["origem"], "nota")

    def test_assumir_devolver_encerrar(self):
        from atendimento.services import obter_conversa
        self.client.post(reverse("painel:conversa_assumir", args=[self.conversa.pk]))
        self.conversa.refresh_from_db()
        self.assertEqual((self.conversa.status, self.conversa.atendente), ("humano", self.admin))
        self.client.post(reverse("painel:conversa_devolver", args=[self.conversa.pk]))
        self.conversa.refresh_from_db()
        self.assertEqual((self.conversa.status, self.conversa.atendente), ("robo", None))
        self.client.post(reverse("painel:conversa_encerrar", args=[self.conversa.pk]))
        self.conversa.refresh_from_db()
        self.assertEqual(self.conversa.status, "encerrada")
        nova = obter_conversa(self.unidade, "5531999990000")
        self.assertNotEqual(nova.pk, self.conversa.pk)  # próxima mensagem abre conversa nova
        self.assertEqual(nova.status, "robo")

    def test_conversa_encerrada_nao_aceita_resposta(self):
        self.conversa.status = "encerrada"
        self.conversa.save()
        resp = self.client.post(reverse("painel:conversa_responder", args=[self.conversa.pk]), {"texto": "oi"})
        self.assertEqual(resp.status_code, 409)

    def test_voltar_so_para_conversas_do_proprio_site(self):
        resp = self.client.post(reverse("painel:conversa_assumir", args=[self.conversa.pk]),
                                {"voltar": "https://site-malicioso.com/conversas/"})
        self.assertNotIn("malicioso", resp["Location"])

    def test_atualizacoes_traz_so_mensagens_novas(self):
        primeira = self.conversa.mensagens.first()
        Mensagem.objects.create(conversa=self.conversa, origem="paciente", texto="alô?")
        resp = self.client.get(reverse("painel:conversa_atualizacoes", args=[self.conversa.pk]), {"depois": primeira.pk})
        self.assertEqual([m["texto"] for m in resp.json()["mensagens"]], ["alô?"])

class ResumoProtegidoTests(TestCase):
    def test_save_completo_de_objeto_antigo_nao_apaga_resumo(self):
        from atendimento.models import Conversa
        clinica = Clinica.objects.create(nome="Teste")
        unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000")
        conversa = Conversa.objects.create(paciente=paciente, unidade=unidade)
        antiga = Conversa.objects.get(pk=conversa.pk)  # carregada ANTES da mensagem
        Mensagem.objects.create(conversa=conversa, origem="paciente", texto="oi")
        antiga.status = "humano"
        antiga.save()  # save completo
        conversa.refresh_from_db()
        self.assertEqual((conversa.status, conversa.ultima_mensagem_texto), ("humano", "oi"))

@override_settings(WEBHOOK_TOKEN=TOKEN)
class TiposDeMensagemTests(TestCase):
    def setUp(self):
        unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")
        InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="clinica-recife")
        self.url = reverse("integracoes:webhook_evolution") + f"?token={TOKEN}"
        patcher = mock.patch("integracoes.views.disparar_assistente")
        self.disparar = patcher.start()
        self.addCleanup(patcher.stop)

    def enviar(self, tipo, message, msg_id="T1"):
        corpo = payload_mensagem(msg_id=msg_id)
        corpo["data"]["messageType"] = tipo
        corpo["data"]["message"] = message
        self.client.post(self.url, json.dumps(corpo), content_type="application/json")
        return Mensagem.objects.filter(id_externo=msg_id).first()

    def test_reacao_figurinha_e_protocolo_sao_ignorados(self):
        for i, tipo in enumerate(["reactionMessage", "stickerMessage", "protocolMessage", "albumMessage", "secretEncryptedMessage"]):
            self.assertIsNone(self.enviar(tipo, {tipo: {}}, msg_id=f"I{i}"))
        self.assertFalse(Mensagem.objects.exists())
        self.disparar.assert_not_called()

    def test_imagem_com_legenda(self):
        m = self.enviar("imageMessage", {"imageMessage": {"caption": "meu dente quebrou"}})
        self.assertEqual(m.texto, "📷 Imagem\nmeu dente quebrou")
        self.disparar.assert_called_once()

    def test_audio_documento_e_documento_com_legenda(self):
        self.assertEqual(self.enviar("audioMessage", {"audioMessage": {"seconds": 12}}, "A1").texto, "🎤 Áudio")
        self.assertEqual(self.enviar("documentMessage", {"documentMessage": {"fileName": "raio-x.pdf"}}, "D1").texto,
                         "📄 Documento: raio-x.pdf")
        embrulhado = {"documentWithCaptionMessage": {"message": {"documentMessage": {"fileName": "exame.pdf", "caption": "segue"}}}}
        self.assertEqual(self.enviar("documentWithCaptionMessage", embrulhado, "D2").texto, "📄 Documento: exame.pdf\nsegue")

    def test_texto_normal_continua_igual(self):
        self.assertEqual(self.enviar("conversation", {"conversation": "oi"}).texto, "oi")
        self.assertEqual(self.enviar("extendedTextMessage", {"extendedTextMessage": {"text": "link"}}, "E1").texto, "link")

    def test_tipo_desconhecido_fica_registrado(self):
        m = self.enviar("tipoNovoQualquer", {"tipoNovoQualquer": {}})
        self.assertIn("tipoNovoQualquer", m.texto)

@override_settings(WEBHOOK_TOKEN=TOKEN)
class WebhookConfirmacaoTests(TestCase):
    def setUp(self):
        unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")
        InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="clinica-recife")
        self.url = reverse("integracoes:webhook_evolution") + f"?token={TOKEN}"

    @mock.patch("integracoes.views.disparar_assistente")
    @mock.patch("integracoes.views.tratar_resposta_confirmacao", return_value=True)
    def test_sim_tratado_nao_aciona_assistente(self, tratar, disparar):
        self.client.post(self.url, json.dumps(payload_mensagem(texto="sim", msg_id="S1")), content_type="application/json")
        tratar.assert_called_once()
        disparar.assert_not_called()

    @mock.patch("integracoes.views.disparar_assistente")
    @mock.patch("integracoes.views.tratar_resposta_confirmacao", return_value=False)
    def test_outra_mensagem_segue_para_o_assistente(self, _tratar, disparar):
        self.client.post(self.url, json.dumps(payload_mensagem(texto="oi", msg_id="S2")), content_type="application/json")
        disparar.assert_called_once()


class BotaoLembretesTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))

    @mock.patch("atendimento.lembretes.executar")
    def test_botao_envia_ignorando_a_janela(self, executar):
        from atendimento.lembretes import ResultadoEnvio
        executar.return_value = ResultadoEnvio(confirmacoes=2, lembretes=1)
        resp = self.client.post(reverse("painel:assistente_lembretes_agora"), follow=True)
        executar.assert_called_once_with(ignorar_janela=True)
        self.assertContains(resp, "2 pedido(s) de confirmação e 1 lembrete(s)")

    def test_salvar_configuracao_de_lembretes(self):
        self.client.post(reverse("painel:assistente"), {
            "formulario": "lembretes", "lembretes-confirmacao_ativa": "on",
            "lembretes-confirmacao_hora": "09:30", "lembretes-lembrete_horas_antes": "3",
        })
        c = Configuracao.obter()
        self.assertEqual((c.confirmacao_hora.strftime("%H:%M"), c.lembrete_horas_antes), ("09:30", 3))

    def test_hora_fora_da_janela_e_recusada(self):
        self.client.post(reverse("painel:assistente"), {
            "formulario": "lembretes", "lembretes-confirmacao_ativa": "on",
            "lembretes-confirmacao_hora": "22:00", "lembretes-lembrete_horas_antes": "2",
        })
        self.assertEqual(Configuracao.obter().confirmacao_hora.strftime("%H:%M"), "10:00")

@override_settings(WEBHOOK_TOKEN=TOKEN)
class MenosGravacoesNoWebhookTests(TestCase):
    def test_estado_repetido_nao_grava_de_novo(self):
        unidade = Unidade.objects.create(clinica=Clinica.objects.create(nome="Teste"), nome="Recife")
        instancia = InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="clinica-recife")
        url = reverse("integracoes:webhook_evolution") + f"?token={TOKEN}"
        evento = {"event": "connection.update", "instance": "clinica-recife", "data": {"state": "open"}}
        self.client.post(url, json.dumps(evento), content_type="application/json")
        instancia.refresh_from_db()
        primeira = instancia.ultima_verificacao
        with mock.patch("integracoes.models.InstanciaWhatsApp.save") as salvar:
            for _ in range(3):
                self.client.post(url, json.dumps(evento), content_type="application/json")
            salvar.assert_not_called()
        instancia.refresh_from_db()
        self.assertEqual((instancia.status, instancia.ultima_verificacao), (StatusConexao.CONECTADO, primeira))