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

from .evolution import EvolutionClient, atualizar_status
from .models import Configuracao, InstanciaWhatsApp, StatusConexao

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
