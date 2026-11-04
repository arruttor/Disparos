import io
import json
from datetime import datetime, timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from atendimento.models import Conversa, Mensagem
from core.models import Clinica, Unidade
from integracoes.evolution import ErroEvolution, EvolutionClient
from integracoes.models import InstanciaWhatsApp
from .forms import FormConfiguracao, FormContato
from .importacao import importar, ler
from .models import ConfiguracaoDisparo, Contato, NumeroReservado, Rodada, Tentativa
from .services import executar_rodada, executar_tentativa, reservar, validar_mensagem
from .telefones import chave, normalizar, variacoes


@override_settings(MODO_DISPAROS=True, ROOT_URLCONF='config.urls')
class DisparosTests(TestCase):
    def setUp(self):
        self.clinica = Clinica.objects.create(nome='Clínica de teste')
        self.unidade = Unidade.objects.create(clinica=self.clinica, nome='Recife')
        self.instancia = InstanciaWhatsApp.objects.create(unidade=self.unidade, nome_instancia='teste', status='conectado')
        self.config = ConfiguracaoDisparo.obter()
        self.config.unidade = self.unidade
        self.config.mensagem = 'Olá, {primeiro_nome}! Tudo bem, {nome}?'
        self.config.ativo = True
        self.config.save()
        self.contato = self.criar_contato('1', '81988887777')
        self.agora = timezone.make_aware(datetime(2026, 11, 3, 9, 0))
        self.api = mock.Mock()
        self.api.verificar_numeros.return_value = [{'exists': True, 'number': '5581988887777', 'jid': '5581988887777@s.whatsapp.net'}]
        self.api.enviar_texto.return_value = {'key': {'id': 'ENVIO-1'}}

    def criar_contato(self, identidade, telefone):
        numero = normalizar(telefone)
        return Contato.objects.create(unidade=self.unidade, id_origem=identidade, nome='Maria Silva',
            telefone_original=telefone, telefone=numero, chave_numero=chave(numero))

    def liberar_intervalo(self):
        ConfiguracaoDisparo.objects.filter(pk=1).update(proxima_tentativa_em=None)

    def reservar(self, contato=None, **kwargs):
        return reservar((contato or self.contato).pk, **kwargs)

    def test_normalizacao_e_alias(self):
        self.assertEqual(normalizar('+55 (81) 98888-7777'), '5581988887777')
        self.assertEqual(chave('81988887777'), chave('8188887777'))
        self.assertEqual(len(variacoes('81988887777')), 2)
        for valor in ('123', '2015550123', 'telefone 81988887777', '81988887777 / 81999998888'):
            self.assertEqual(normalizar(valor), '')

    def test_reserva_antes_da_rede_e_envio_unico(self):
        t = self.reservar()
        self.assertTrue(NumeroReservado.objects.exists())
        self.assertIsNotNone(Contato.objects.get(pk=self.contato.pk).automatico_reservado_em)
        executar_tentativa(t.pk, self.api)
        executar_tentativa(t.pk, self.api)
        self.api.enviar_texto.assert_called_once_with('teste', '5581988887777', 'Olá, Maria! Tudo bem, Maria Silva?')
        self.assertEqual(Tentativa.objects.get(pk=t.pk).status, 'enviado')
        self.liberar_intervalo()
        self.assertIsNone(self.reservar())

    def test_falha_na_validacao_nunca_retorna_automaticamente(self):
        self.api.verificar_numeros.side_effect = ErroEvolution('offline')
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        self.liberar_intervalo()
        self.assertIsNone(self.reservar())
        self.api.enviar_texto.assert_not_called()
        self.assertEqual(Tentativa.objects.get(pk=t.pk).status, 'falha')

    def test_timeout_envio_e_incerto_sem_retry(self):
        self.api.enviar_texto.side_effect = ErroEvolution('timeout')
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        self.liberar_intervalo()
        self.assertIsNone(self.reservar())
        executar_tentativa(t.pk, self.api)
        self.api.enviar_texto.assert_called_once()
        self.assertEqual(Tentativa.objects.get(pk=t.pk).status, 'incerto')

    def test_resposta_sem_id_e_incerta(self):
        self.api.enviar_texto.return_value = {}
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        self.assertEqual(Tentativa.objects.get(pk=t.pk).status, 'incerto')

    def test_redelivery_apos_crash_apos_claim_nao_reenvia(self):
        t = self.reservar()
        Tentativa.objects.filter(pk=t.pk).update(chamada_iniciada_em=timezone.now())
        executar_tentativa(t.pk, self.api)
        self.api.verificar_numeros.assert_not_called()

    def test_dois_pacientes_com_mesmo_numero_sao_bloqueados(self):
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        outro = self.criar_contato('2', '8188887777')
        self.liberar_intervalo()
        duplicado = self.reservar(outro)
        executar_tentativa(duplicado.pk, self.api)
        self.assertEqual(Tentativa.objects.get(pk=duplicado.pk).status, 'bloqueado')
        self.api.enviar_texto.assert_called_once()

    def test_reserva_global_entre_unidades(self):
        self.reservar()
        outra = Unidade.objects.create(clinica=self.clinica, nome='Minas')
        InstanciaWhatsApp.objects.create(unidade=outra, nome_instancia='minas', status='conectado')
        ConfiguracaoDisparo.objects.filter(pk=1).update(unidade=outra, proxima_tentativa_em=None)
        self.contato.unidade = outra
        self.contato.pk = None
        self.contato.id_origem = 'outro'
        self.contato.save()
        t = reservar(self.contato.pk)
        self.assertEqual(Tentativa.objects.get(pk=t.pk).status, 'bloqueado')

    def test_manual_permitido_apos_falha_e_auditado(self):
        self.api.verificar_numeros.side_effect = ErroEvolution('offline')
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        self.api.verificar_numeros.side_effect = None
        self.liberar_intervalo()
        usuario = User.objects.create_user('operador')
        manual = self.reservar(manual=True, autor=usuario)
        executar_tentativa(manual.pk, self.api)
        self.assertTrue(manual.manual)
        self.assertEqual(manual.autor_id, usuario.pk)
        self.api.enviar_texto.assert_called_once()

    def test_manual_nao_repete_envio_aceito(self):
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        self.liberar_intervalo()
        self.assertIsNone(self.reservar(manual=True))

    def test_manual_nao_contorna_reserva_de_outro_paciente(self):
        self.reservar()
        self.liberar_intervalo()
        outro = self.criar_contato('2', '8188887777')
        t = self.reservar(outro)
        self.liberar_intervalo()
        manual = self.reservar(outro, manual=True)
        self.assertEqual(Tentativa.objects.get(pk=manual.pk).status, 'bloqueado')

    def test_correcao_confirmada_antes_de_um_unico_envio(self):
        self.api.verificar_numeros.return_value = [{'exists': True, 'number': '5581988887777', 'jid': '558188887777@s.whatsapp.net'}]
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        self.api.enviar_texto.assert_called_once_with('teste', '558188887777', t.mensagem)
        self.contato.refresh_from_db()
        self.assertEqual(self.contato.telefone_original, '81988887777')

    def test_numero_inexistente_ambiguo_ou_alheio_nao_envia(self):
        for resposta in ([], [{'exists': True, 'number': '5581988887777', 'jid': '5511999999999@s.whatsapp.net'}],
            [{'exists': True, 'number': '5581988887777', 'jid': '5581988887777@s.whatsapp.net'},
             {'exists': True, 'number': '558188887777', 'jid': '558188887777@s.whatsapp.net'}]):
            self.api.verificar_numeros.return_value = resposta
            self.liberar_intervalo()
            if self.contato.tentativas.exists():
                t = self.reservar(manual=True)
            else:
                t = self.reservar()
            executar_tentativa(t.pk, self.api)
        self.api.enviar_texto.assert_not_called()

    def test_pausa_antes_do_envio_preserva_reserva(self):
        t = self.reservar()
        ConfiguracaoDisparo.objects.filter(pk=1).update(ativo=False)
        executar_tentativa(t.pk, self.api)
        self.api.enviar_texto.assert_not_called()
        self.assertEqual(Tentativa.objects.get(pk=t.pk).status, 'pausado')

    def test_pausa_durante_validacao(self):
        resposta = self.api.verificar_numeros.return_value
        def pausar(*args):
            ConfiguracaoDisparo.objects.filter(pk=1).update(ativo=False)
            return resposta
        self.api.verificar_numeros.side_effect = pausar
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        self.api.enviar_texto.assert_not_called()

    def test_agendador_respeita_horario_lote_intervalo(self):
        ConfiguracaoDisparo.objects.filter(pk=1).update(tamanho_lote=1)
        outro = self.criar_contato('2', '81977776666')
        self.assertIsNone(executar_rodada(self.agora - timedelta(minutes=1), self.api))
        self.assertIsNotNone(executar_rodada(self.agora, self.api))
        self.assertIsNone(executar_rodada(self.agora + timedelta(minutes=2), self.api))
        self.assertEqual(Rodada.objects.get().reservados, 1)
        outro.refresh_from_db()
        self.assertIsNone(outro.automatico_reservado_em)

    def test_nao_recupera_janelas_antigas(self):
        self.assertIsNone(executar_rodada(self.agora + timedelta(hours=2), self.api))
        self.assertIsNone(executar_rodada(self.agora.replace(hour=21), self.api))
        self.api.enviar_texto.assert_not_called()

    def test_intervalo_global_e_desconexao(self):
        self.reservar()
        outro = self.criar_contato('2', '81977776666')
        self.assertIsNone(self.reservar(outro))
        self.liberar_intervalo()
        InstanciaWhatsApp.objects.update(status='desconectado')
        self.assertIsNone(self.reservar(outro))

    def planilha(self, linhas=None):
        wb = Workbook()
        ws = wb.active
        ws.append(['Relatório'])
        ws.append(['ID interno', 'Nome', 'Celular', 'Grupo', 'Prontuário', 'Situação'])
        for linha in linhas or [['1', 'Maria Silva', '81988887777', 'Geral', '001', 'Ativo']]:
            ws.append(linha)
        conteudo = io.BytesIO()
        wb.save(conteudo)
        return SimpleUploadedFile('pacientes.xlsx', conteudo.getvalue())

    def test_importacao_idempotente_preserva_reserva_e_original(self):
        t = self.reservar()
        executar_tentativa(t.pk, self.api)
        importar(self.planilha([['1', 'Maria Atualizada', '81977776666']]), self.unidade)
        self.contato.refresh_from_db()
        self.assertEqual(Contato.objects.count(), 1)
        self.assertIsNotNone(self.contato.automatico_reservado_em)
        self.assertEqual(self.contato.telefone_original, '81988887777')
        self.liberar_intervalo()
        self.assertIsNone(self.reservar())

    def test_importacao_invalida_nao_faz_gravacao_parcial(self):
        with self.assertRaises(ValueError):
            importar(self.planilha([['2', 'Nova', '81999998888'], ['', 'Inválida', '123']]), self.unidade)
        self.assertEqual(Contato.objects.count(), 1)

    def test_importacao_telefones_invalidos_vao_para_revisao(self):
        importar(self.planilha([['2', 'Nova', '123']]), self.unidade)
        self.assertTrue(Contato.objects.get(id_origem='2').revisao)

    def test_importacao_rejeita_formulas(self):
        with self.assertRaises(ValueError):
            ler(self.planilha([['2', '=1+1', '81999998888']]))

    def test_edicao_preserva_original_e_bloqueio(self):
        self.reservar()
        self.contato.refresh_from_db()
        form = FormContato({'nome': 'Maria', 'telefone': '(81) 97777-6666', 'selecionado': True}, instance=self.contato)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.contato.refresh_from_db()
        self.assertEqual(self.contato.telefone_original, '81988887777')
        self.assertIsNotNone(self.contato.automatico_reservado_em)

    def test_mensagem_rejeita_campos_arbitrarios(self):
        for texto in ('', 'Oi {nome.__class__}', 'Oi {nome:>100000000}', '{campo}', '{nome!r}'):
            with self.assertRaises(ValueError):
                validar_mensagem(texto)

    def test_webhook_ignora_todas_as_conversas(self):
        with override_settings(WEBHOOK_TOKEN='teste'):
            for from_me in (False, True):
                resposta = self.client.post('/integracoes/webhook/evolution/?token=teste', data=json.dumps({
                    'event': 'messages.upsert', 'instance': 'teste', 'data': {
                        'key': {'fromMe': from_me, 'id': 'X', 'remoteJid': '5581999999999@s.whatsapp.net'},
                        'message': {'conversation': 'Oi'}}}), content_type='application/json')
                self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Conversa.objects.exists())
        self.assertFalse(Mensagem.objects.exists())

    def test_legado_desligado_mesmo_com_tarefas_antigas(self):
        from atendimento import tasks
        from atendimento.lembretes import executar
        from atendimento.services import processar_turno
        with mock.patch('integracoes.evolution.EvolutionClient.enviar_texto') as envio:
            tasks.enviar_lembretes()
            tasks.processar_turno(1, 1)
            processar_turno(1, 1)
            executar(ignorar_janela=True)
            envio.assert_not_called()

    def test_telas_protegidas_e_sem_rotas_antigas(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        usuario = User.objects.create_superuser('admin', 'a@example.com', 'senha-teste')
        self.client.force_login(usuario)
        for url in ('/', '/pacientes/', '/disparos/historico/', '/integracoes/', f'/pacientes/{self.contato.pk}/'):
            resposta = self.client.get(url)
            self.assertEqual(resposta.status_code, 200, url)
            self.assertNotContains(resposta, '>Assistente</a>')
            self.assertNotContains(resposta, '>Conversas</a>')
        for url in ('/assistente/', '/conversas/', '/integracoes/google/1/'):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(f'/pacientes/{self.contato.pk}/tentar/').status_code, 405)

    def test_validacao_configuracao(self):
        form = FormConfiguracao({'unidade': self.unidade.pk, 'mensagem': 'Oi {nome}',
            'horarios': '07:00', 'tamanho_lote': 0, 'intervalo_segundos': 0}, instance=self.config)
        self.assertFalse(form.is_valid())
        for lote, intervalo, campo in ((0, 60, 'tamanho_lote'), (51, 60, 'tamanho_lote'), (10, 0, 'intervalo_segundos'), (10, 59, 'intervalo_segundos')):
            form = FormConfiguracao({'unidade': self.unidade.pk, 'mensagem': 'Oi {nome}',
                'horarios': '09:00', 'tamanho_lote': lote, 'intervalo_segundos': intervalo}, instance=self.config)
            self.assertFalse(form.is_valid())
            self.assertIn(campo, form.errors)

    def test_evolution_validacao_contrato(self):
        from integracoes.models import Configuracao
        config = Configuracao(evolution_url='http://localhost:8080', evolution_api_key='teste')
        with mock.patch.object(EvolutionClient, '_req', return_value=[]) as req:
            EvolutionClient(config).verificar_numeros('teste', ['5581999999999'])
        req.assert_called_once_with('POST', '/chat/whatsappNumbers/teste', json={'numbers': ['5581999999999']})

    def test_reentrada_durante_validacao_nao_duplica_chamada(self):
        t = self.reservar()
        resposta = self.api.verificar_numeros.return_value
        def concorrente(*args):
            executar_tentativa(t.pk, self.api)
            self.assertIsNone(self.reservar())
            return resposta
        self.api.verificar_numeros.side_effect = concorrente
        executar_tentativa(t.pk, self.api)
        self.api.verificar_numeros.assert_called_once()
        self.api.enviar_texto.assert_called_once()

    def test_formulario_antigo_nao_limpa_reserva(self):
        form = FormContato({'nome': 'Maria', 'telefone': '81988887777', 'selecionado': True}, instance=self.contato)
        self.assertTrue(form.is_valid())
        self.reservar()
        form.save()
        self.contato.refresh_from_db()
        self.assertIsNotNone(self.contato.automatico_reservado_em)

    def test_mesmo_id_nova_unidade_novo_numero_permanece_bloqueado(self):
        self.reservar()
        outra = Unidade.objects.create(clinica=self.clinica, nome='Outra')
        InstanciaWhatsApp.objects.create(unidade=outra, nome_instancia='outra', status='conectado')
        ConfiguracaoDisparo.objects.filter(pk=1).update(unidade=outra, proxima_tentativa_em=None)
        outro = Contato.objects.create(unidade=outra, id_origem='1', nome='Maria', telefone_original='81977776666',
            telefone='5581977776666', chave_numero=chave('81977776666'))
        t = reservar(outro.pk)
        self.assertEqual(Tentativa.objects.get(pk=t.pk).status, 'bloqueado')

    def test_numero_corrigido_nao_perde_correcao_na_reimportacao(self):
        self.contato.telefone = '5581977776666'
        self.contato.save(update_fields=['telefone'])
        importar(self.planilha(), self.unidade)
        self.contato.refresh_from_db()
        self.assertEqual(self.contato.telefone, '5581977776666')
        self.assertEqual(self.contato.telefone_original, '81988887777')

    def test_teste_manual_reserva_numero_mesmo_com_fila_pausada(self):
        ConfiguracaoDisparo.objects.filter(pk=1).update(ativo=False)
        t = self.reservar(manual=True, teste=True)
        executar_tentativa(t.pk, self.api)
        self.api.enviar_texto.assert_called_once()
        self.liberar_intervalo()
        ConfiguracaoDisparo.objects.filter(pk=1).update(ativo=True)
        self.assertIsNone(self.reservar())

    def test_selecao_em_lote_nao_libera_reserva(self):
        self.reservar()
        usuario = User.objects.create_superuser('admin', 'a@example.com', 'senha-teste')
        self.client.force_login(usuario)
        for acao in ('excluir', 'incluir'):
            self.assertEqual(self.client.post('/pacientes/selecao/', {'contatos': [self.contato.pk], 'acao': acao}).status_code, 302)
        self.contato.refresh_from_db()
        self.assertTrue(self.contato.selecionado)
        self.assertIsNotNone(self.contato.automatico_reservado_em)

    def test_usuario_comum_nao_pode_operar_disparos(self):
        usuario = User.objects.create_user('comum', password='senha-teste')
        self.client.force_login(usuario)
        for url in ('/', '/pacientes/', '/disparos/historico/', '/integracoes/'):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_configuracao_ativa_nao_pode_ser_editada(self):
        usuario = User.objects.create_superuser('admin', 'a@example.com', 'senha-teste')
        self.client.force_login(usuario)
        self.client.post('/', {'unidade': self.unidade.pk, 'mensagem': 'Outra mensagem {nome}',
            'horarios': '10:00', 'tamanho_lote': 2, 'intervalo_segundos': 60})
        self.config.refresh_from_db()
        self.assertEqual(self.config.mensagem, 'Olá, {primeiro_nome}! Tudo bem, {nome}?')

    def test_admin_legado_sem_permissoes(self):
        usuario = User.objects.create_superuser('admin', 'a@example.com', 'senha-teste')
        self.client.force_login(usuario)
        for url in ('/admin/atendimento/conversa/', '/admin/integracoes/configuracao/', '/admin/integracoes/contagoogle/'):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_csv_compativel_com_cabecalhos_acentuados(self):
        arquivo = SimpleUploadedFile('base.csv', 'ID interno;Nome;Celular;Prontuário\n2;José;81977776666;0002\n'.encode('utf-8-sig'))
        self.assertEqual(importar(arquivo, self.unidade), (1, 0, 0))
        self.assertEqual(Contato.objects.get(id_origem='2').prontuario, '0002')
