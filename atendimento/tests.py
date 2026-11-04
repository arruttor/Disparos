import json
from datetime import date, datetime, time, timedelta
from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from core.models import Clinica, Dentista, HorarioAtendimento, Procedimento, Unidade
from integracoes.google import ErroGoogle
from integracoes.models import ContaGoogle

from .agenda import buscar_horarios_livres, calcular_horarios_livres
from .models import Agendamento, Paciente


def dt(dia, hora, minuto=0):
    return timezone.make_aware(datetime.combine(dia, time(hora, minuto)))


DIA = date(2026, 10, 5)  # segunda-feira
AGORA = dt(DIA - timedelta(days=1), 9)  # véspera: antecedência não interfere


class CalcularHorariosLivresTests(SimpleTestCase):
    def test_janela_livre_gera_horarios_a_cada_30_min(self):
        livres = calcular_horarios_livres([(dt(DIA, 8), dt(DIA, 10))], 30, [], AGORA)
        self.assertEqual(livres, [dt(DIA, 8), dt(DIA, 8, 30), dt(DIA, 9), dt(DIA, 9, 30)])

    def test_consulta_precisa_caber_inteira_na_janela(self):
        # 90 min numa janela 8h–10h: 8:00 e 8:30 cabem; 9:00 terminaria 10:30.
        livres = calcular_horarios_livres([(dt(DIA, 8), dt(DIA, 10))], 90, [], AGORA)
        self.assertEqual(livres, [dt(DIA, 8), dt(DIA, 8, 30)])

    def test_horario_ocupado_e_sobreposicoes_sao_removidos(self):
        ocupado = [(dt(DIA, 9), dt(DIA, 9, 30))]
        livres = calcular_horarios_livres([(dt(DIA, 8), dt(DIA, 10, 30))], 60, ocupado, AGORA)
        # 8:00–9:00 encosta mas não sobrepõe (fica); 8:30–9:30 e 9:00–10:00 sobrepõem (saem).
        self.assertEqual(livres, [dt(DIA, 8), dt(DIA, 9, 30)])

    def test_respeita_antecedencia_minima(self):
        agora = dt(DIA, 8, 10)
        livres = calcular_horarios_livres([(dt(DIA, 8), dt(DIA, 12))], 30, [], agora, antecedencia_minutos=120)
        self.assertEqual(livres[0], dt(DIA, 10, 30))

    def test_duas_janelas_no_mesmo_dia(self):
        janelas = [(dt(DIA, 8), dt(DIA, 9)), (dt(DIA, 14), dt(DIA, 15))]
        livres = calcular_horarios_livres(janelas, 60, [], AGORA)
        self.assertEqual(livres, [dt(DIA, 8), dt(DIA, 14)])

    def test_sem_janela_sem_horario(self):
        self.assertEqual(calcular_horarios_livres([], 30, [], AGORA), [])


class BuscarHorariosLivresTests(TestCase):
    def setUp(self):
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Recife")
        self.dentista = Dentista.objects.create(unidade=self.unidade, nome="Dra. Ana", google_calendar_id="cal-ana")
        HorarioAtendimento.objects.create(dentista=self.dentista, dia_semana=0, inicio=time(8), fim=time(10))
        self.procedimento = Procedimento.objects.create(clinica=clinica, nome="Avaliação", duracao_minutos=60)
        self.paciente = Paciente.objects.create(clinica=clinica, telefone="5581999990000")

    def test_sem_conta_google_lanca_erro(self):
        with self.assertRaises(ErroGoogle):
            buscar_horarios_livres(self.dentista, self.procedimento, DIA, agora=AGORA)

    @mock.patch("atendimento.agenda.GoogleAgenda")
    def test_desconta_google_e_agendamentos_locais(self, agenda_cls):
        ContaGoogle.objects.create(unidade=self.unidade, token_json='{"access_token": "x"}')
        agenda_cls.return_value.ocupados.return_value = {"cal-ana": [(dt(DIA, 8), dt(DIA, 8, 30))]}
        Agendamento.objects.create(
            paciente=self.paciente, dentista=self.dentista, procedimento=self.procedimento,
            inicio=dt(DIA, 9), fim=dt(DIA, 10),
        )

        resultado = buscar_horarios_livres(self.dentista, self.procedimento, DIA, dias=2, agora=AGORA)

        # Janela 8–10, consulta de 60 min: 8:00 bate no Google, 8:30 e 9:00 batem no agendamento local.
        self.assertEqual(resultado, {})
        self.assertEqual(agenda_cls.return_value.ocupados.call_args.args[0], ["cal-ana"])

    @mock.patch("atendimento.agenda.GoogleAgenda")
    def test_agendamento_cancelado_nao_ocupa(self, agenda_cls):
        ContaGoogle.objects.create(unidade=self.unidade, token_json='{"access_token": "x"}')
        agenda_cls.return_value.ocupados.return_value = {}
        Agendamento.objects.create(
            paciente=self.paciente, dentista=self.dentista, procedimento=self.procedimento,
            inicio=dt(DIA, 8), fim=dt(DIA, 9), status=Agendamento.Status.CANCELADO,
        )
        resultado = buscar_horarios_livres(self.dentista, self.procedimento, DIA, agora=AGORA)
        self.assertEqual(resultado, {DIA: [dt(DIA, 8), dt(DIA, 8, 30), dt(DIA, 9)]})

    @mock.patch("atendimento.agenda.GoogleAgenda")
    def test_calendario_vazio_usa_principal(self, agenda_cls):
        self.dentista.google_calendar_id = ""
        self.dentista.save()
        ContaGoogle.objects.create(unidade=self.unidade, token_json='{"access_token": "x"}')
        agenda_cls.return_value.ocupados.return_value = {}
        buscar_horarios_livres(self.dentista, self.procedimento, DIA, agora=AGORA)
        self.assertEqual(agenda_cls.return_value.ocupados.call_args.args[0], ["primary"])


class AgendamentoServicoTests(TestCase):
    """marcar / confirmar / cancelar / remarcar — Google simulado."""

    def setUp(self):
        from . import agendamentos
        self.svc = agendamentos
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        ContaGoogle.objects.create(unidade=self.unidade, token_json='{"access_token": "x"}')
        self.dentista = Dentista.objects.create(unidade=self.unidade, nome="Dra. Ana", google_calendar_id="cal-ana")
        HorarioAtendimento.objects.create(dentista=self.dentista, dia_semana=0, inicio=time(8), fim=time(12))
        self.proc = Procedimento.objects.create(clinica=clinica, nome="Avaliação", duracao_minutos=30)
        self.paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000", nome="Maria Souza")
        # Google: nada ocupado; criar_evento devolve um id.
        patcher = mock.patch("atendimento.agenda.GoogleAgenda")
        self.google_agenda = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.google_agenda.ocupados.return_value = {}
        patcher2 = mock.patch("atendimento.agendamentos.GoogleAgenda")
        self.google_escrita = patcher2.start().return_value
        self.addCleanup(patcher2.stop)
        self.google_escrita.criar_evento.return_value = {"id": "evt-1"}
        # "agora" fixo na véspera, para a antecedência não interferir
        patcher3 = mock.patch("atendimento.agenda.timezone.now", return_value=AGORA)
        patcher3.start()
        self.addCleanup(patcher3.stop)

    def test_marcar_cria_registro_e_evento(self):
        ag = self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.assertEqual((ag.google_event_id, ag.google_calendar_id, ag.status), ("evt-1", "cal-ana", "aguardando"))
        self.assertEqual(ag.fim, dt(DIA, 9, 30))
        cal, corpo = self.google_escrita.criar_evento.call_args.args
        self.assertEqual(cal, "cal-ana")
        self.assertTrue(corpo["summary"].startswith("⏳ Maria Souza"))
        self.assertEqual((corpo["colorId"], corpo["transparency"]), ("5", "opaque"))
        self.assertEqual(corpo["extendedProperties"]["private"]["agendamento_id"], str(ag.pk))

    def test_horario_ocupado_no_google_nao_marca(self):
        self.google_agenda.ocupados.return_value = {"cal-ana": [(dt(DIA, 9), dt(DIA, 10))]}
        with self.assertRaisesMessage(self.svc.ErroAgendamento, "não está mais disponível"):
            self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.assertFalse(Agendamento.objects.exists())
        self.google_escrita.criar_evento.assert_not_called()

    def test_horario_fora_do_atendimento_nao_marca(self):
        with self.assertRaises(self.svc.ErroAgendamento):
            self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 13))  # atende só até 12h

    def test_falha_no_google_nao_deixa_registro_fantasma(self):
        self.google_escrita.criar_evento.side_effect = ErroGoogle("fora do ar")
        with self.assertRaises(self.svc.ErroAgendamento):
            self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.assertFalse(Agendamento.objects.exists())

    def test_mesmo_horario_nao_pode_ser_marcado_duas_vezes(self):
        self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        with self.assertRaises(self.svc.ErroAgendamento):
            self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.assertEqual(Agendamento.objects.count(), 1)

    def test_confirmar_muda_cor_e_prefixo(self):
        ag = self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.svc.confirmar(ag)
        ag.refresh_from_db()
        self.assertEqual(ag.status, "confirmado")
        self.assertIsNotNone(ag.confirmado_em)
        cal, evento, corpo = self.google_escrita.atualizar_evento.call_args.args
        self.assertEqual((cal, evento, corpo["colorId"]), ("cal-ana", "evt-1", "10"))
        self.assertTrue(corpo["summary"].startswith("✅"))

    def test_cancelar_libera_horario_mas_mantem_evento_visivel(self):
        ag = self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.svc.cancelar(ag, motivo="paciente desmarcou")
        corpo = self.google_escrita.atualizar_evento.call_args.args[2]
        self.assertEqual((corpo["colorId"], corpo["transparency"]), ("11", "transparent"))
        self.assertIn("paciente desmarcou", corpo["description"])
        # O mesmo horário pode ser marcado de novo
        self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))

    def test_cancelar_evento_apagado_no_google_ainda_cancela(self):
        from integracoes.google import ErroEventoNaoEncontrado
        ag = self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.google_escrita.atualizar_evento.side_effect = ErroEventoNaoEncontrado("sumiu")
        self.svc.cancelar(ag)
        ag.refresh_from_db()
        self.assertEqual(ag.status, "cancelado")

    def test_confirmar_com_falha_no_google_desfaz(self):
        ag = self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.google_escrita.atualizar_evento.side_effect = ErroGoogle("fora do ar")
        with self.assertRaises(self.svc.ErroAgendamento):
            self.svc.confirmar(ag)
        ag.refresh_from_db()
        self.assertEqual(ag.status, "aguardando")

    def test_remarcar_para_horario_sobreposto_ao_proprio(self):
        ag = self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        self.svc.confirmar(ag)
        # Consulta de 60 min (9:00–10:00); o Google mostra a própria consulta como ocupada.
        self.proc.duracao_minutos = 60
        self.proc.save()
        ag.fim = dt(DIA, 10)
        ag.save()
        self.google_agenda.ocupados.return_value = {"cal-ana": [(dt(DIA, 9), dt(DIA, 10))]}
        self.svc.remarcar(ag, dt(DIA, 9, 30))  # sobrepõe 9:30–10:00 do horário antigo dela
        ag.refresh_from_db()
        self.assertEqual((ag.inicio, ag.fim, ag.status), (dt(DIA, 9, 30), dt(DIA, 10, 30), "aguardando"))
        self.assertIsNone(ag.confirmado_em)
        self.assertTrue(self.google_escrita.atualizar_evento.call_args.args[2]["summary"].startswith("⏳"))

    def test_remarcar_para_horario_de_outro_paciente_falha(self):
        ag = self.svc.marcar(self.paciente, self.dentista, self.proc, dt(DIA, 9))
        outro = Paciente.objects.create(clinica=self.unidade.clinica, telefone="5531988880000")
        self.svc.marcar(outro, self.dentista, self.proc, dt(DIA, 10))
        with self.assertRaises(self.svc.ErroAgendamento):
            self.svc.remarcar(ag, dt(DIA, 10))
        ag.refresh_from_db()
        self.assertEqual(ag.inicio, dt(DIA, 9))


class SubtrairIntervaloTests(SimpleTestCase):
    def test_casos(self):
        from .agenda import subtrair_intervalo
        oc = [(dt(DIA, 8), dt(DIA, 12))]
        self.assertEqual(subtrair_intervalo(oc, (dt(DIA, 9), dt(DIA, 10))), [(dt(DIA, 8), dt(DIA, 9)), (dt(DIA, 10), dt(DIA, 12))])
        self.assertEqual(subtrair_intervalo(oc, (dt(DIA, 7), dt(DIA, 13))), [])
        self.assertEqual(subtrair_intervalo(oc, (dt(DIA, 13), dt(DIA, 14))), oc)


class NormalizarTelefoneTests(SimpleTestCase):
    def test_formatos(self):
        from .services import normalizar_telefone
        self.assertEqual(normalizar_telefone("(81) 99999-0000"), "5581999990000")
        self.assertEqual(normalizar_telefone("+55 31 98888-1111"), "5531988881111")
        self.assertEqual(normalizar_telefone("3133334444"), "553133334444")
        self.assertEqual(normalizar_telefone("12345"), "")
        self.assertEqual(normalizar_telefone("1 555 123 4567"), "")  # número dos EUA
        self.assertEqual(normalizar_telefone("553188881111"), "553188881111")  # celular antigo, como vem do WhatsApp
        self.assertEqual(normalizar_telefone("(01) 99999-0000"), "")  # DDD inválido

# ---------------------------------------------------------------------------
# Assistente (API simulada)
# ---------------------------------------------------------------------------
from types import SimpleNamespace as NS  # noqa: E402


def bloco_texto(texto):
    return NS(type="text", text=texto)


def bloco_ferramenta(nome, entrada, id_="tu_1"):
    return NS(type="tool_use", id=id_, name=nome, input=entrada)


def resposta_api(*blocos, stop="end_turn"):
    return NS(stop_reason=stop, content=list(blocos), stop_details=None,
              usage=NS(input_tokens=100, output_tokens=20, cache_read_input_tokens=0))


class ClienteFalso:
    """Imita client.beta.messages.create devolvendo respostas em sequência."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        import copy
        self.chamadas.append(copy.deepcopy(kwargs))
        return self.respostas.pop(0)


class AssistenteTests(TestCase):
    def setUp(self):
        from integracoes.models import Configuracao
        config = Configuracao.obter()
        config.ia_api_key = "sk-teste"
        config.assistente_nome = "Clara"
        config.save()
        clinica = Clinica.objects.create(nome="Clínica Sorriso")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas", cidade="BH")
        self.dentista = Dentista.objects.create(unidade=self.unidade, nome="Dra. Ana")
        self.proc = Procedimento.objects.create(clinica=clinica, nome="Limpeza", duracao_minutos=30)
        self.paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000")
        from .models import Conversa, Mensagem
        self.Mensagem = Mensagem
        self.conversa = Conversa.objects.create(paciente=self.paciente, unidade=self.unidade, simulada=True)
        Mensagem.objects.create(conversa=self.conversa, origem="paciente", texto="Oi, quero marcar uma limpeza")

    def responder(self, cliente):
        from .assistente import responder
        return responder(self.conversa, cliente=cliente)

    def test_resposta_simples_e_parametros_da_api(self):
        cliente = ClienteFalso(resposta_api(bloco_texto("Claro! Qual dia fica melhor?")))
        r = self.responder(cliente)
        self.assertEqual(r.texto, "Claro! Qual dia fica melhor?")
        kw = cliente.chamadas[0]
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual((kw["fallbacks"], kw["betas"]), ("default", ["server-side-fallback-2026-07-01"]))
        self.assertEqual(kw["output_config"], {"effort": "medium"})
        self.assertNotIn("thinking", kw)  # Opus 5.5: thinking sempre ligado; não se envia
        self.assertNotIn("tool_choice", kw)  # forçar ferramenta dá 400 no Opus 5.5
        self.assertTrue(all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in kw["tools"]))
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("Clara", kw["system"][0]["text"])
        self.assertIn("Nunca informe preços", kw["system"][0]["text"])
        self.assertNotIn("cache_control", kw["system"][1])  # parte do turno (data/paciente) fica fora do cache
        self.assertEqual(kw["messages"], [{"role": "user", "content": "Oi, quero marcar uma limpeza"}])

    @mock.patch("atendimento.assistente.buscar_horarios_livres")
    def test_usa_ferramenta_e_devolve_resultado(self, buscar):
        buscar.return_value = {DIA: [dt(DIA, 9), dt(DIA, 9, 30)]}
        cliente = ClienteFalso(
            resposta_api(bloco_ferramenta("buscar_horarios_livres",
                         {"procedimento_id": self.proc.pk, "data_inicial": "2099-01-01", "dentista_id": None}), stop="tool_use"),
            resposta_api(bloco_texto("Tenho segunda às 9h ou 9h30. Qual prefere?")),
        )
        r = self.responder(cliente)
        self.assertIn("9h", r.texto)
        segunda = cliente.chamadas[1]["messages"]
        self.assertEqual(segunda[1]["role"], "assistant")  # resposta da API devolvida completa
        resultado = segunda[2]["content"][0]
        self.assertEqual((resultado["type"], resultado["tool_use_id"]), ("tool_result", "tu_1"))
        self.assertIn("09:00, 09:30", resultado["content"])
        self.assertNotIn("is_error", resultado)

    def test_marcar_sem_nome_vira_erro_para_o_modelo(self):
        cliente = ClienteFalso(
            resposta_api(bloco_ferramenta("marcar_consulta", {"procedimento_id": self.proc.pk, "dentista_id": self.dentista.pk,
                         "inicio": "2099-01-05T09:00", "observacoes": ""}), stop="tool_use"),
            resposta_api(bloco_texto("Antes, qual é o seu nome completo?")),
        )
        self.responder(cliente)
        resultado = cliente.chamadas[1]["messages"][2]["content"][0]
        self.assertTrue(resultado["is_error"])
        self.assertIn("nome", resultado["content"])

    @mock.patch("atendimento.agendamentos.marcar")
    def test_marcar_com_nome_chama_agendamento_como_assistente(self, marcar):
        self.paciente.nome = "Maria"
        self.paciente.save()
        marcar.return_value = mock.Mock(pk=7, inicio=dt(DIA, 9))
        cliente = ClienteFalso(
            resposta_api(bloco_ferramenta("marcar_consulta", {"procedimento_id": self.proc.pk, "dentista_id": self.dentista.pk,
                         "inicio": "2099-01-05T09:00", "observacoes": "limpeza de rotina"}), stop="tool_use"),
            resposta_api(bloco_texto("Prontinho, Maria! Marcado.")),
        )
        r = self.responder(cliente)
        self.assertEqual(marcar.call_args.kwargs["origem"], "assistente")
        self.assertEqual(marcar.call_args.args[3].isoformat(), "2099-01-05T09:00:00-03:00")
        self.assertIn("marcou consulta 7", r.acoes[0])

    def test_nao_mexe_em_consulta_de_outro_paciente(self):
        from .models import Agendamento
        outro = Paciente.objects.create(clinica=self.unidade.clinica, telefone="5531911110000")
        ag = Agendamento.objects.create(paciente=outro, dentista=self.dentista, procedimento=self.proc,
                                        inicio=dt(DIA, 9), fim=dt(DIA, 9, 30))
        cliente = ClienteFalso(
            resposta_api(bloco_ferramenta("cancelar_consulta", {"consulta_id": ag.pk, "motivo": "x"}), stop="tool_use"),
            resposta_api(bloco_texto("Não encontrei essa consulta.")),
        )
        self.responder(cliente)
        self.assertTrue(cliente.chamadas[1]["messages"][2]["content"][0]["is_error"])
        ag.refresh_from_db()
        self.assertEqual(ag.status, "aguardando")

    def test_transferir_para_humano(self):
        cliente = ClienteFalso(
            resposta_api(bloco_ferramenta("transferir_para_humano", {"motivo": "pediu atendente", "resumo": "quer falar com alguém"}), stop="tool_use"),
            resposta_api(bloco_texto("Já chamei a equipe!")),
        )
        r = self.responder(cliente)
        self.conversa.refresh_from_db()
        self.assertTrue(r.transferiu)
        self.assertEqual((self.conversa.status, self.conversa.motivo_transferencia), ("humano", "pediu atendente"))

    def test_recusa_de_seguranca_transfere(self):
        recusa = resposta_api(stop="refusal")
        cliente = ClienteFalso(recusa)
        r = self.responder(cliente)
        self.conversa.refresh_from_db()
        self.assertTrue(r.transferiu)
        self.assertEqual(r.texto, "")
        self.assertEqual(self.conversa.status, "humano")

    def test_historico_comeca_no_paciente_junta_e_identifica_equipe(self):
        from datetime import timedelta as td
        from .assistente import montar_historico
        M = self.Mensagem
        M.objects.all().delete()
        antiga = M.objects.create(conversa=self.conversa, origem="paciente", texto="mensagem de ontem de ontem")
        M.objects.filter(pk=antiga.pk).update(criada_em=timezone.now() - td(days=5))
        M.objects.create(conversa=self.conversa, origem="robo", texto="sobra do robô")
        M.objects.create(conversa=self.conversa, origem="paciente", texto="oi")
        M.objects.create(conversa=self.conversa, origem="paciente", texto="tudo bem?")
        M.objects.create(conversa=self.conversa, origem="humano", texto="Oi, aqui é a Júlia")
        M.objects.create(conversa=self.conversa, origem="paciente", texto="quero marcar")
        self.assertEqual(montar_historico(self.conversa), [
            {"role": "user", "content": "oi\ntudo bem?"},
            {"role": "assistant", "content": "[Mensagem enviada pela equipe da clínica]: Oi, aqui é a Júlia"},
            {"role": "user", "content": "quero marcar"},
        ])

    def test_sem_mensagem_nova_do_paciente_nao_chama_api(self):
        self.Mensagem.objects.create(conversa=self.conversa, origem="robo", texto="já respondi")
        cliente = ClienteFalso()
        self.assertEqual(self.responder(cliente).texto, "")
        self.assertEqual(cliente.chamadas, [])


class ProcessarTurnoTests(TestCase):
    def setUp(self):
        from integracoes.models import Configuracao
        Configuracao.obter()
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        self.paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000")
        from .models import Conversa, Mensagem
        self.conversa = Conversa.objects.create(paciente=self.paciente, unidade=self.unidade, simulada=True)
        self.M = Mensagem

    @mock.patch("atendimento.assistente.responder")
    def test_mensagem_mais_nova_responde_pelas_duas(self, responder):
        from .services import processar_turno
        m1 = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="oi")
        self.M.objects.create(conversa=self.conversa, origem="paciente", texto="quero marcar")
        self.assertIsNone(processar_turno(self.conversa.pk, m1.pk, esperar=False))
        responder.assert_not_called()

    @mock.patch("atendimento.assistente.responder", side_effect=RuntimeError("API fora"))
    def test_falha_da_ia_avisa_paciente_e_transfere(self, _responder):
        from .assistente import MENSAGEM_FALHA
        from .services import processar_turno
        m = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="oi")
        processar_turno(self.conversa.pk, m.pk, esperar=False)
        self.conversa.refresh_from_db()
        self.assertEqual(self.conversa.status, "humano")
        self.assertEqual(self.conversa.mensagens.last().texto, MENSAGEM_FALHA)

    @mock.patch("atendimento.assistente.responder")
    def test_transferencia_sem_texto_manda_aviso_padrao(self, responder):
        from .assistente import Resultado
        from .services import TEXTO_TRANSFERENCIA, processar_turno
        responder.return_value = Resultado(texto="", transferiu=True)
        m = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="quero reclamar")
        processar_turno(self.conversa.pk, m.pk, esperar=False)
        self.assertEqual(self.conversa.mensagens.last().texto, TEXTO_TRANSFERENCIA)


class EcoDoRoboTests(TestCase):
    """A mensagem que o robô envia volta pelo webhook (fromMe) — não pode ser tratada como equipe."""

    def setUp(self):
        from integracoes.models import Configuracao, InstanciaWhatsApp
        Configuracao.obter()
        clinica = Clinica.objects.create(nome="Teste")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        InstanciaWhatsApp.objects.create(unidade=self.unidade, nome_instancia="clinica-minas-2")
        paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000")
        from .models import Conversa
        self.conversa = Conversa.objects.create(paciente=paciente, unidade=self.unidade)

    @mock.patch("integracoes.evolution.EvolutionClient")
    def test_eco_antes_do_retorno_do_envio(self, cliente_cls):
        from .services import enviar_resposta, registrar_mensagem

        def enviar_e_ecoar(instancia, numero, texto):
            # O webhook do eco chega ANTES de o envio retornar.
            registrar_mensagem(self.unidade, numero, texto, "ID-REAL", enviada_por_nos=True)
            return {"key": {"id": "ID-REAL"}}

        cliente_cls.return_value.enviar_texto.side_effect = enviar_e_ecoar
        msg = enviar_resposta(self.conversa, "Olá! Como posso ajudar?")
        msg.refresh_from_db()
        self.conversa.refresh_from_db()
        self.assertEqual((msg.origem, msg.id_externo), ("robo", "ID-REAL"))
        self.assertIsNone(self.conversa.robo_pausado_ate)  # não confundiu com a equipe
        self.assertEqual(self.conversa.mensagens.count(), 1)

    @mock.patch("integracoes.evolution.EvolutionClient")
    def test_eco_depois_do_retorno_e_ignorado_pelo_id(self, cliente_cls):
        from .services import enviar_resposta, registrar_mensagem
        cliente_cls.return_value.enviar_texto.return_value = {"key": {"id": "ID-2"}}
        enviar_resposta(self.conversa, "Oi!")
        self.assertEqual(registrar_mensagem(self.unidade, "5531999990000", "Oi!", "ID-2", enviada_por_nos=True), (None, None))
        self.conversa.refresh_from_db()
        self.assertIsNone(self.conversa.robo_pausado_ate)

    def test_mensagem_da_equipe_continua_pausando(self):
        from .services import registrar_mensagem
        registrar_mensagem(self.unidade, "5531999990000", "Oi, aqui é a Júlia", "ID-H", enviada_por_nos=True)
        self.conversa.refresh_from_db()
        self.assertIsNotNone(self.conversa.robo_pausado_ate)

# ---------------------------------------------------------------------------
# Provedor compatível com OpenAI (Gemini) — API simulada
# ---------------------------------------------------------------------------


def resposta_oa(content=None, tool_calls=None, finish="stop"):
    """Monta uma resposta no formato da OpenAI usando os tipos reais do SDK."""
    from openai.types.chat import ChatCompletion
    mensagem = {"role": "assistant", "content": content}
    if tool_calls:
        mensagem["tool_calls"] = tool_calls
    return ChatCompletion.model_validate({
        "id": "r1", "object": "chat.completion", "created": 0, "model": "gemini-3.8-flash",
        "choices": [{"index": 0, "finish_reason": finish, "message": mensagem}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
    })


def chamada_oa(nome, argumentos, id_="call_1", assinatura="ASSINATURA-GEMINI"):
    return {"id": id_, "type": "function", "function": {"name": nome, "arguments": json.dumps(argumentos)},
            "extra_content": {"google": {"thought_signature": assinatura}}}


class ClienteOAFalso:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        import copy
        self.chamadas.append(copy.deepcopy(kwargs))
        return self.respostas.pop(0)


class GeminiTests(TestCase):
    def setUp(self):
        from integracoes.models import Configuracao
        config = Configuracao.obter()
        config.ia_provedor = "gemini"
        config.ia_modelo = "gemini-3.8-flash"
        config.ia_api_key = "chave-gemini"
        config.save()
        clinica = Clinica.objects.create(nome="Clínica Sorriso")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        self.dentista = Dentista.objects.create(unidade=self.unidade, nome="Dra. Ana")
        self.proc = Procedimento.objects.create(clinica=clinica, nome="Limpeza", duracao_minutos=30)
        paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000")
        from .models import Conversa, Mensagem
        self.conversa = Conversa.objects.create(paciente=paciente, unidade=self.unidade, simulada=True)
        Mensagem.objects.create(conversa=self.conversa, origem="paciente", texto="Oi, quero marcar uma limpeza")

    def responder(self, cliente):
        from .assistente import responder
        return responder(self.conversa, cliente=cliente)

    def test_formato_da_requisicao(self):
        cliente = ClienteOAFalso(resposta_oa("Olá! Qual dia prefere?"))
        r = self.responder(cliente)
        self.assertEqual(r.texto, "Olá! Qual dia prefere?")
        kw = cliente.chamadas[0]
        self.assertEqual((kw["model"], kw["tool_choice"], kw["reasoning_effort"]), ("gemini-3.8-flash", "auto", "low"))
        self.assertEqual(kw["messages"][0]["role"], "system")
        self.assertIn("Nunca informe preços", kw["messages"][0]["content"])
        self.assertIn("Hoje é", kw["messages"][0]["content"])  # parte do turno junto
        self.assertEqual(kw["messages"][1], {"role": "user", "content": "Oi, quero marcar uma limpeza"})

    def test_ferramentas_no_subconjunto_que_o_gemini_aceita(self):
        from .assistente import ferramentas_compativeis
        ferramentas = {f["function"]["name"]: f["function"] for f in ferramentas_compativeis()}
        self.assertEqual(len(ferramentas), 8)
        texto = json.dumps(ferramentas)
        self.assertNotIn("additionalProperties", texto)
        self.assertNotIn('"null"', texto)
        self.assertNotIn("strict", texto)
        busca = ferramentas["buscar_horarios_livres"]["parameters"]
        self.assertEqual(busca["properties"]["dentista_id"]["type"], "integer")
        self.assertNotIn("dentista_id", busca["required"])
        self.assertNotIn("parameters", ferramentas["listar_consultas_do_paciente"])  # sem propriedades

    @mock.patch("atendimento.assistente.buscar_horarios_livres")
    def test_assinatura_do_gemini_volta_intacta(self, buscar):
        buscar.return_value = {DIA: [dt(DIA, 9)]}
        cliente = ClienteOAFalso(
            resposta_oa(tool_calls=[chamada_oa("buscar_horarios_livres",
                        {"procedimento_id": self.proc.pk, "data_inicial": "2099-01-01"})], finish="tool_calls"),
            resposta_oa("Tenho segunda às 9h. Pode ser?"),
        )
        r = self.responder(cliente)
        self.assertIn("9h", r.texto)
        msgs = cliente.chamadas[1]["messages"]
        assistente_msg, ferramenta_msg = msgs[-2], msgs[-1]
        self.assertEqual(assistente_msg["tool_calls"][0]["extra_content"],
                         {"google": {"thought_signature": "ASSINATURA-GEMINI"}})
        self.assertEqual((ferramenta_msg["role"], ferramenta_msg["tool_call_id"]), ("tool", "call_1"))
        self.assertIn("09:00", ferramenta_msg["content"])
        # dentista_id omitido = busca nos dentistas da unidade (aqui, só a Dra. Ana)
        self.assertEqual([c.args[0] for c in buscar.call_args_list], [self.dentista])

    def test_erro_de_ferramenta_vai_como_texto_de_erro(self):
        cliente = ClienteOAFalso(
            resposta_oa(tool_calls=[chamada_oa("marcar_consulta", {"procedimento_id": self.proc.pk,
                        "dentista_id": self.dentista.pk, "inicio": "2099-01-05T09:00", "observacoes": ""})], finish="tool_calls"),
            resposta_oa("Qual é o seu nome completo?"),
        )
        self.responder(cliente)
        retorno = cliente.chamadas[1]["messages"][-1]["content"]
        self.assertTrue(retorno.startswith("ERRO:"))
        self.assertIn("nome", retorno)

    def test_argumentos_invalidos_e_campo_faltando(self):
        quebrada = {"id": "c1", "type": "function", "function": {"name": "cancelar_consulta", "arguments": "{nao é json"}}
        faltando = {"id": "c2", "type": "function", "function": {"name": "confirmar_consulta", "arguments": "{}"}}
        cliente = ClienteOAFalso(resposta_oa(tool_calls=[quebrada, faltando], finish="tool_calls"), resposta_oa("Desculpe!"))
        self.responder(cliente)
        retornos = [m["content"] for m in cliente.chamadas[1]["messages"] if m.get("role") == "tool"]
        self.assertIn("JSON inválido", retornos[0])
        self.assertIn("consulta_id", retornos[1])

    def test_filtro_de_conteudo_transfere(self):
        cliente = ClienteOAFalso(resposta_oa(None, finish="content_filter"))
        r = self.responder(cliente)
        self.conversa.refresh_from_db()
        self.assertTrue(r.transferiu)
        self.assertEqual(self.conversa.status, "humano")

def erro_503():
    import openai
    return openai.InternalServerError("high demand", response=mock.Mock(status_code=503, headers={}), body=None)


class ClienteOAComErros(ClienteOAFalso):
    """Como ClienteOAFalso, mas um item da sequência pode ser uma exceção a lançar."""

    def _create(self, **kwargs):
        import copy
        self.chamadas.append(copy.deepcopy(kwargs))
        item = self.respostas.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ModeloReservaTests(GeminiTests):
    def test_sobrecarga_na_primeira_chamada_usa_reserva(self):
        cliente = ClienteOAComErros(erro_503(), resposta_oa("Olá! Como posso ajudar?"))
        r = self.responder(cliente)
        self.assertEqual(r.texto, "Olá! Como posso ajudar?")
        self.assertEqual([c["model"] for c in cliente.chamadas], ["gemini-3.8-flash", "gemini-3.5-flash"])
        from core.models import RegistroLog
        self.assertTrue(RegistroLog.objects.filter(nivel="WARNING", mensagem__contains="usando o reserva").exists())

    @mock.patch("atendimento.assistente.buscar_horarios_livres", return_value={})
    def test_sobrecarga_no_meio_do_turno_nao_troca_de_modelo(self, _buscar):
        cliente = ClienteOAComErros(
            resposta_oa(tool_calls=[chamada_oa("buscar_horarios_livres",
                        {"procedimento_id": self.proc.pk, "data_inicial": "2099-01-01"})], finish="tool_calls"),
            erro_503(),
        )
        import openai
        with self.assertRaises(openai.InternalServerError):
            self.responder(cliente)
        self.assertEqual([c["model"] for c in cliente.chamadas], ["gemini-3.8-flash", "gemini-3.8-flash"])

    def test_reserva_igual_ao_principal_nao_repete(self):
        from integracoes.models import Configuracao
        config = Configuracao.obter()
        config.ia_modelo_reserva = "gemini-3.8-flash"
        config.save()
        import openai
        with self.assertRaises(openai.InternalServerError):
            self.responder(ClienteOAComErros(erro_503()))


class DevolverAoAssistenteTests(TestCase):
    def test_acao_do_admin(self):
        from django.contrib.auth.models import User
        from django.urls import reverse
        from .models import Conversa
        clinica = Clinica.objects.create(nome="Teste")
        unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000")
        conversa = Conversa.objects.create(paciente=paciente, unidade=unidade, status="humano",
                                           motivo_transferencia="Falha do assistente", robo_pausado_ate=timezone.now())
        self.client.force_login(User.objects.create_superuser("admin", "a@a.com", "senha-forte-123"))
        self.client.post(reverse("admin:atendimento_conversa_changelist"),
                         {"action": "devolver_ao_assistente", "_selected_action": [conversa.pk]})
        conversa.refresh_from_db()
        self.assertEqual((conversa.status, conversa.motivo_transferencia, conversa.robo_pausado_ate), ("robo", "", None))

# ---------------------------------------------------------------------------
# Confirmação D-1 e lembrete do dia
# ---------------------------------------------------------------------------


class EhConfirmacaoTests(SimpleTestCase):
    def test_sim_e_variacoes(self):
        from .lembretes import eh_confirmacao
        for t in ["sim", "Sim!", "SIM", "s", "confirmo", "Confirmado.", "pode confirmar", "ok", "1", "👍", "sim, obrigada", "Sim confirmo"]:
            with self.subTest(t):
                self.assertTrue(eh_confirmacao(t))

    def test_duvida_ou_mudanca_nao_e_sim(self):
        from .lembretes import eh_confirmacao
        for t in ["não", "nao vou poder", "sim, mas posso remarcar?", "quero mudar o horário", "sim?", "talvez",
                  "sim, mas preciso cancelar", "oi, tudo bem? queria saber se a consulta de amanhã continua de pé mesmo"]:
            with self.subTest(t):
                self.assertFalse(eh_confirmacao(t))


class LembretesTests(TestCase):
    def setUp(self):
        from integracoes.models import Configuracao, InstanciaWhatsApp
        from .models import Agendamento
        self.Agendamento = Agendamento
        Configuracao.obter()
        clinica = Clinica.objects.create(nome="Clínica Sorriso")
        self.unidade = Unidade.objects.create(clinica=clinica, nome="Minas", endereco="Rua A, 10")
        InstanciaWhatsApp.objects.create(unidade=self.unidade, nome_instancia="clinica-minas-2")
        self.dentista = Dentista.objects.create(unidade=self.unidade, nome="Dra. Ana")
        self.proc = Procedimento.objects.create(clinica=clinica, nome="Limpeza", duracao_minutos=30)
        self.paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000", nome="Maria Souza")
        # "agora" = segunda 05/10/2026 às 11h (dentro da janela; depois das 10h da confirmação)
        self.agora = dt(DIA, 11)
        patcher = mock.patch("atendimento.lembretes._enviar_para_paciente", return_value=True)
        self.enviar = patcher.start()
        self.addCleanup(patcher.stop)

    def consulta(self, inicio, criada=None, **extra):
        ag = self.Agendamento.objects.create(paciente=self.paciente, dentista=self.dentista, procedimento=self.proc,
                                             inicio=inicio, fim=inicio + timedelta(minutes=30), **extra)
        self.Agendamento.objects.filter(pk=ag.pk).update(criado_em=criada or self.agora - timedelta(days=3))
        ag.refresh_from_db()
        return ag

    def executar(self, agora=None, **kw):
        from .lembretes import executar
        return executar(agora or self.agora, **kw)

    def test_confirmacao_vai_para_consulta_de_amanha(self):
        amanha = self.consulta(dt(DIA + timedelta(days=1), 14, 30))
        self.consulta(dt(DIA + timedelta(days=2), 9))  # depois de amanhã: ainda não
        r = self.executar()
        self.assertEqual((r.confirmacoes, r.lembretes), (1, 0))
        ag_enviado, texto = self.enviar.call_args.args
        self.assertEqual(ag_enviado, amanha)
        self.assertIn("amanhã, *terça, 06/10, às 14h30*", texto)
        self.assertIn("Responda *SIM*", texto)
        amanha.refresh_from_db()
        self.assertIsNotNone(amanha.lembrete_enviado_em)

    def test_nao_manda_duas_vezes(self):
        self.consulta(dt(DIA + timedelta(days=1), 14))
        self.executar()
        self.assertEqual(self.executar().confirmacoes, 0)

    def test_respeita_hora_configurada_e_janela(self):
        self.consulta(dt(DIA + timedelta(days=1), 14))
        self.assertEqual(self.executar(dt(DIA, 9, 30)).confirmacoes, 0)  # antes das 10h configuradas
        r = self.executar(dt(DIA, 21))  # fora da janela 8h–20h
        self.assertEqual((r.confirmacoes, r.motivo), (0, "Fora do horário de envio (8h às 20h)."))
        self.assertEqual(self.executar(dt(DIA, 21), ignorar_janela=True).confirmacoes, 1)  # botão do painel

    def test_nao_pede_confirmacao_de_quem_acabou_de_marcar_ou_ja_confirmou(self):
        self.consulta(dt(DIA + timedelta(days=1), 14), criada=self.agora - timedelta(minutes=30))
        self.consulta(dt(DIA + timedelta(days=1), 15), status="confirmado")
        self.consulta(dt(DIA + timedelta(days=1), 16), status="cancelado")
        self.assertEqual(self.executar().confirmacoes, 0)

    def test_desativada_nao_envia(self):
        from integracoes.models import Configuracao
        c = Configuracao.obter()
        c.confirmacao_ativa = False
        c.save()
        self.consulta(dt(DIA + timedelta(days=1), 14))
        self.assertEqual(self.executar().confirmacoes, 0)

    def test_lembrete_do_dia_duas_horas_antes(self):
        hoje = self.consulta(dt(DIA, 12, 30), status="confirmado")  # 1h30 depois de "agora"
        self.consulta(dt(DIA, 15))  # ainda falta mais de 2h
        r = self.executar()
        self.assertEqual(r.lembretes, 1)
        ag_enviado, texto = self.enviar.call_args.args
        self.assertEqual(ag_enviado, hoje)
        self.assertIn("hoje às 12h30", texto)
        self.assertIn("Rua A, 10", texto)

    def test_lembrete_nao_vai_para_quem_marcou_em_cima_da_hora(self):
        self.consulta(dt(DIA, 12), criada=dt(DIA, 10, 30))
        self.assertEqual(self.executar().lembretes, 0)

    def test_falha_no_envio_nao_marca_e_tenta_de_novo(self):
        self.enviar.return_value = False
        ag = self.consulta(dt(DIA + timedelta(days=1), 14))
        self.assertEqual(self.executar().falhas, 1)
        ag.refresh_from_db()
        self.assertIsNone(ag.lembrete_enviado_em)
        self.enviar.return_value = True
        self.assertEqual(self.executar().confirmacoes, 1)

    def test_paciente_do_simulador_nao_recebe(self):
        self.paciente.telefone = "sim1"
        self.paciente.save()
        self.consulta(dt(DIA + timedelta(days=1), 14))
        self.assertEqual(self.executar().confirmacoes, 0)
        self.enviar.assert_not_called()


class RespostaConfirmacaoTests(TestCase):
    def setUp(self):
        from integracoes.models import Configuracao
        from .models import Agendamento, Conversa, Mensagem
        self.M = Mensagem
        Configuracao.obter()
        clinica = Clinica.objects.create(nome="Clínica Sorriso")
        unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        ContaGoogle.objects.create(unidade=unidade, token_json='{"access_token": "x"}')
        dentista = Dentista.objects.create(unidade=unidade, nome="Dra. Ana")
        proc = Procedimento.objects.create(clinica=clinica, nome="Limpeza", duracao_minutos=30)
        self.paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000", nome="Maria")
        self.conversa = Conversa.objects.create(paciente=self.paciente, unidade=unidade, simulada=True)
        inicio = timezone.now() + timedelta(days=1)
        self.ag = Agendamento.objects.create(paciente=self.paciente, dentista=dentista, procedimento=proc,
                                             inicio=inicio, fim=inicio + timedelta(minutes=30), google_event_id="evt-1",
                                             lembrete_enviado_em=timezone.now() - timedelta(hours=1))
        patcher = mock.patch("atendimento.agendamentos.GoogleAgenda")
        self.google = patcher.start().return_value
        self.addCleanup(patcher.stop)

    def responder(self, texto):
        from .lembretes import tratar_resposta_confirmacao
        msg = self.M.objects.create(conversa=self.conversa, origem="paciente", texto=texto)
        return tratar_resposta_confirmacao(self.conversa, msg)

    def test_sim_confirma_atualiza_google_e_responde(self):
        self.assertTrue(self.responder("Sim!"))
        self.ag.refresh_from_db()
        self.assertEqual(self.ag.status, "confirmado")
        self.assertEqual(self.google.atualizar_evento.call_args.args[2]["colorId"], "10")
        self.assertTrue(self.conversa.mensagens.last().texto.startswith("Consulta confirmada ✅"))

    def test_outra_resposta_fica_com_o_assistente(self):
        self.assertFalse(self.responder("não vou poder, dá pra remarcar?"))
        self.ag.refresh_from_db()
        self.assertEqual(self.ag.status, "aguardando")

    def test_sim_sem_pedido_recente_nao_confirma(self):
        self.ag.lembrete_enviado_em = timezone.now() - timedelta(days=3)
        self.ag.save()
        self.assertFalse(self.responder("sim"))

    def test_sim_sem_pedido_enviado_nao_confirma(self):
        self.ag.lembrete_enviado_em = None
        self.ag.save()
        self.assertFalse(self.responder("sim"))


class FilaDeTarefasTests(TestCase):
    def setUp(self):
        from integracoes.models import Configuracao, InstanciaWhatsApp
        from .models import Conversa, Mensagem
        Configuracao.obter()
        clinica = Clinica.objects.create(nome="Teste")
        unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="clinica-minas-2", robo_ativo=True)
        paciente = Paciente.objects.create(clinica=clinica, telefone="5531999990000")
        self.conversa = Conversa.objects.create(paciente=paciente, unidade=unidade)
        self.msg = Mensagem.objects.create(conversa=self.conversa, origem="paciente", texto="oi")

    @mock.patch("atendimento.services.celery_tem_worker", return_value=True)
    @mock.patch("atendimento.tasks.processar_turno.apply_async")
    def test_com_fila_agenda_tarefa_com_a_espera(self, apply_async, _worker):
        from django.test import override_settings
        from .services import disparar_assistente
        with override_settings(ASSISTENTE_EM_SEGUNDO_PLANO=True, CELERY_BROKER_URL="redis://localhost:6379/0"):
            disparar_assistente(self.conversa, self.msg)
        self.assertEqual(apply_async.call_args.kwargs["args"], (self.conversa.pk, self.msg.pk))
        self.assertEqual(apply_async.call_args.kwargs["countdown"], 20)  # "oi" é só cumprimento: 8 + 12

    @mock.patch("atendimento.services.celery_tem_worker", return_value=True)
    @mock.patch("atendimento.services.threading.Thread")
    @mock.patch("atendimento.tasks.processar_turno.apply_async", side_effect=ConnectionError("redis fora"))
    def test_fila_fora_do_ar_volta_para_thread(self, _apply, thread, _worker):
        from django.test import override_settings
        from .services import disparar_assistente
        with override_settings(ASSISTENTE_EM_SEGUNDO_PLANO=True, CELERY_BROKER_URL="redis://localhost:6379/0"):
            disparar_assistente(self.conversa, self.msg)
        thread.return_value.start.assert_called_once()

    @mock.patch("atendimento.services.threading.Thread")
    @mock.patch("atendimento.tasks.processar_turno.apply_async")
    def test_sem_fila_usa_thread(self, apply_async, thread):
        from django.test import override_settings
        from .services import disparar_assistente
        with override_settings(ASSISTENTE_EM_SEGUNDO_PLANO=True, CELERY_BROKER_URL=""):
            disparar_assistente(self.conversa, self.msg)
        apply_async.assert_not_called()
        thread.return_value.start.assert_called_once()

    @mock.patch("atendimento.services.celery_tem_worker", return_value=False)
    @mock.patch("atendimento.services.threading.Thread")
    @mock.patch("atendimento.tasks.processar_turno.apply_async")
    def test_fila_sem_worker_nao_usa_a_fila(self, apply_async, thread, _worker):
        """Redis de pé mas sem worker: a tarefa ficaria parada e o paciente sem resposta."""
        from django.test import override_settings
        from .services import disparar_assistente
        with override_settings(ASSISTENTE_EM_SEGUNDO_PLANO=True, CELERY_BROKER_URL="redis://localhost:6379/0"):
            disparar_assistente(self.conversa, self.msg)
        apply_async.assert_not_called()
        thread.return_value.start.assert_called_once()

class EnvioManualTests(LembretesTests):
    def test_botao_envia_mesmo_para_quem_acabou_de_marcar_e_antes_da_hora(self):
        self.consulta(dt(DIA + timedelta(days=1), 14), criada=dt(DIA, 9, 10))
        self.assertEqual(self.executar(dt(DIA, 9, 15)).confirmacoes, 0)  # automático: carência + antes das 10h
        self.assertEqual(self.executar(dt(DIA, 9, 15), ignorar_janela=True).confirmacoes, 1)  # botão

class AvisoSemWorkerTests(TestCase):
    @mock.patch("atendimento.services.celery_tem_worker", return_value=False)
    @mock.patch("atendimento.services.threading.Thread")
    def test_aviso_sai_uma_vez_so(self, _thread, _worker):
        from django.test import override_settings
        from core.models import RegistroLog
        from integracoes.models import Configuracao, InstanciaWhatsApp
        from . import services
        from .models import Conversa, Mensagem
        services._aviso_sem_worker["em"] = -1e9
        Configuracao.obter()
        clinica = Clinica.objects.create(nome="Teste")
        unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="x", robo_ativo=True)
        conversa = Conversa.objects.create(paciente=Paciente.objects.create(clinica=clinica, telefone="5531999990000"), unidade=unidade)
        with override_settings(ASSISTENTE_EM_SEGUNDO_PLANO=True, CELERY_BROKER_URL="redis://localhost:6379/0"):
            for texto in ("oi", "tudo bem?", "quero marcar"):
                services.disparar_assistente(conversa, Mensagem.objects.create(conversa=conversa, origem="paciente", texto=texto))
        self.assertEqual(RegistroLog.objects.filter(mensagem__startswith="Fila configurada").count(), 1)

class MensagensSeguidasTests(TestCase):
    """O paciente manda "oi" / "boa tarde" / "quero marcar" separado: uma resposta só, no contexto todo."""

    def setUp(self):
        from integracoes.models import Configuracao, InstanciaWhatsApp
        from .models import Conversa, Mensagem
        self.M = Mensagem
        Configuracao.obter()
        clinica = Clinica.objects.create(nome="Teste")
        unidade = Unidade.objects.create(clinica=clinica, nome="Minas")
        InstanciaWhatsApp.objects.create(unidade=unidade, nome_instancia="x", robo_ativo=True)
        self.conversa = Conversa.objects.create(
            paciente=Paciente.objects.create(clinica=clinica, telefone="5531999990000"), unidade=unidade, simulada=True)

    def test_espera_maior_para_cumprimento(self):
        from .services import eh_so_cumprimento, tempo_de_espera
        for t in ["oi", "Oi!", "Boa tarde", "boa tarde, tudo bem?", "Olá", "bom dia"]:
            with self.subTest(t):
                self.assertTrue(eh_so_cumprimento(t))
        for t in ["quero marcar uma consulta", "oi, quero marcar uma limpeza", "estou com dor"]:
            with self.subTest(t):
                self.assertFalse(eh_so_cumprimento(t))
        self.assertEqual(tempo_de_espera(self.M(texto="boa tarde")), 20)
        self.assertEqual(tempo_de_espera(self.M(texto="quero marcar uma consulta")), 8)

    @mock.patch("atendimento.assistente.responder")
    def test_resposta_velha_e_descartada_se_chegou_mensagem_nova(self, responder):
        from .assistente import Resultado
        from .services import processar_turno
        oi = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="oi")

        def gera_enquanto_paciente_escreve(conversa, **kw):
            # Enquanto a IA "pensa" na resposta ao "oi", chega o pedido de verdade.
            self.M.objects.create(conversa=self.conversa, origem="paciente", texto="quero marcar uma consulta")
            return Resultado(texto="Oi! Como posso ajudar?")

        responder.side_effect = gera_enquanto_paciente_escreve
        self.assertIsNone(processar_turno(self.conversa.pk, oi.pk, esperar=False))
        self.assertFalse(self.conversa.mensagens.filter(origem="robo").exists())

    @mock.patch("atendimento.assistente.responder")
    def test_se_ja_fez_uma_acao_a_resposta_sai_mesmo_assim(self, responder):
        from .assistente import Resultado
        from .services import processar_turno
        sim = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="pode marcar às 9h")

        def marca_e_paciente_escreve(conversa, **kw):
            self.M.objects.create(conversa=self.conversa, origem="paciente", texto="obrigado!")
            return Resultado(texto="Marcado para sexta às 9h!", acoes=["marcou consulta 7"])

        responder.side_effect = marca_e_paciente_escreve
        processar_turno(self.conversa.pk, sim.pk, esperar=False)
        self.assertEqual(self.conversa.mensagens.filter(origem="robo").last().texto, "Marcado para sexta às 9h!")

    @mock.patch("atendimento.assistente.responder")
    def test_so_o_turno_da_ultima_mensagem_chama_a_ia(self, responder):
        from .assistente import Resultado
        from .services import processar_turno
        responder.return_value = Resultado(texto="Claro! Para qual procedimento?")
        m1 = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="oi")
        m2 = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="boa tarde")
        m3 = self.M.objects.create(conversa=self.conversa, origem="paciente", texto="quero marcar uma consulta")
        for m in (m1, m2, m3):
            processar_turno(self.conversa.pk, m.pk, esperar=False)
        self.assertEqual(responder.call_count, 1)
        self.assertEqual(self.conversa.mensagens.filter(origem="robo").count(), 1)

    def test_historico_junta_as_mensagens_seguidas(self):
        from .assistente import montar_historico
        for t in ("oi", "boa tarde", "quero marcar uma consulta"):
            self.M.objects.create(conversa=self.conversa, origem="paciente", texto=t)
        self.assertEqual(montar_historico(self.conversa), [{"role": "user", "content": "oi\nboa tarde\nquero marcar uma consulta"}])


# Regressão do código histórico: executado explicitamente no modo antigo, nunca em produção.
from django.test import override_settings as _legado_settings
for _classe in list(globals().values()):
    if isinstance(_classe, type) and _classe.__module__ == __name__ and issubclass(_classe, (TestCase, SimpleTestCase)):
        _legado_settings(MODO_DISPAROS=False, ROOT_URLCONF='config.urls_legacy')(_classe)
