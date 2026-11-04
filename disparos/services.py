"""Reserva durável ANTES da rede. Não existe retry automático, nem após timeout."""
import logging
from datetime import datetime, timedelta
from string import Formatter

from django.db import transaction
from django.utils import timezone

from integracoes.evolution import EvolutionClient
from integracoes.models import InstanciaWhatsApp, StatusConexao
from .models import ConfiguracaoDisparo, Contato, NumeroReservado, Rodada, Tentativa
from .telefones import chave, normalizar, variacoes

logger = logging.getLogger('clinica.disparos')


def validar_mensagem(texto):
    if not texto.strip() or len(texto) > 4000:
        raise ValueError('Escreva uma mensagem com até 4.000 caracteres.')
    try:
        for _, campo, formato, conversao in Formatter().parse(texto):
            if campo is not None and (campo not in ('nome', 'primeiro_nome') or formato or conversao):
                raise ValueError('Use apenas {nome} ou {primeiro_nome}, sem formatação adicional.')
    except ValueError as e:
        raise ValueError('Confira as chaves da mensagem. Use {nome} ou {primeiro_nome}.') from e
    return texto


def horarios_validos(texto):
    try:
        horarios = sorted({datetime.strptime(v.strip(), '%H:%M').time() for v in texto.split(',')})
    except ValueError as e:
        raise ValueError('Informe horários separados por vírgula, como 09:00,14:00,17:00.') from e
    if not horarios or len(horarios) > 10 or any(h.hour < 8 or h.hour >= 20 for h in horarios):
        raise ValueError('Use de 1 a 10 horários entre 08:00 e 19:59.')
    return horarios


def elegiveis(config):
    return Contato.objects.filter(unidade_id=config.unidade_id, selecionado=True,
        excluido_em__isnull=True, automatico_reservado_em__isnull=True, revisao='').exclude(chave_numero='')


def finalizar(tentativa, status, detalhe):
    Tentativa.objects.filter(pk=tentativa.pk).update(status=status, detalhe=detalhe[:255], finalizado_em=timezone.now())


def reservar(contato_id, *, manual=False, autor=None, agora=None, agendado=False, teste=False):
    agora = agora or timezone.now()
    ConfiguracaoDisparo.obter()
    with transaction.atomic():
        config = ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
        contato = Contato.objects.select_for_update().get(pk=contato_id)
        if contato.excluido_em:
            return None
        if not 1 <= config.tamanho_lote <= 50 or not 60 <= config.intervalo_segundos <= 3600:
            return None
        if not config.unidade_id or config.unidade_id != contato.unidade_id:
            return None
        if contato.revisao or not contato.nome.strip() or not normalizar(contato.telefone):
            return None
        if not manual and (not config.ativo or not contato.selecionado or contato.automatico_reservado_em):
            return None
        # Tentativas concorrentes manuais e automáticas são serializadas por este bloqueio.
        if manual:
            ultima = contato.tentativas.first()
            if (not ultima and not teste) or (ultima and ultima.status == Tentativa.Status.ENVIADO):
                return None
            if ultima and ultima.status == Tentativa.Status.RESERVADO and ultima.criado_em > agora - timedelta(minutes=10):
                return None
        if config.proxima_tentativa_em and config.proxima_tentativa_em > agora:
            return None
        validar_mensagem(config.mensagem)
        instancia = InstanciaWhatsApp.objects.filter(unidade_id=config.unidade_id, status=StatusConexao.CONECTADO).first()
        if not instancia:
            return None
        rodada = None
        if agendado:
            local = timezone.localtime(agora)
            horarios = horarios_validos(config.horarios)
            anteriores = [h for h in horarios if h <= local.time().replace(tzinfo=None)]
            if not anteriores or not 8 <= local.hour < 20:
                return None
            horario = anteriores[-1]
            inicio = timezone.make_aware(datetime.combine(local.date(), horario))
            if agora >= inicio + timedelta(hours=1):
                return None
            rodada, _ = Rodada.objects.get_or_create(dia=local.date(), horario=horario.strftime('%H:%M'))
            if rodada.reservados >= config.tamanho_lote:
                return None
        chave_numero = chave(contato.telefone)
        dono = NumeroReservado.objects.filter(chave=chave_numero).first()
        identidade_anterior = Contato.objects.filter(id_origem=contato.id_origem,
            automatico_reservado_em__isnull=False).exclude(pk=contato.pk).exists()
        bloqueado = (dono and (not manual or dono.contato_id != contato.pk)) or identidade_anterior
        texto = config.mensagem.format(nome=contato.nome.strip(), primeiro_nome=contato.nome.split()[0])
        tentativa = Tentativa.objects.create(contato=contato, unidade=config.unidade, instancia=instancia.nome_instancia,
            nome=contato.nome, telefone_original=contato.telefone_original, telefone_usado=contato.telefone,
            mensagem=texto, manual=manual, autor=autor, rodada=rodada)
        if not contato.automatico_reservado_em:
            contato.automatico_reservado_em = agora
            contato.save(update_fields=['automatico_reservado_em'])
        if bloqueado:
            finalizar(tentativa, Tentativa.Status.BLOQUEADO, 'Este número já foi reservado anteriormente. Nenhuma mensagem enviada.')
            return tentativa
        if not dono:
            NumeroReservado.objects.create(chave=chave_numero, contato=contato)
        if rodada:
            rodada.reservados += 1
            rodada.save(update_fields=['reservados'])
        config.proxima_tentativa_em = agora + timedelta(seconds=config.intervalo_segundos)
        config.save(update_fields=['proxima_tentativa_em'])
        return tentativa


def executar_tentativa(tentativa_id, client=None):
    tentativa = Tentativa.objects.get(pk=tentativa_id)
    # Claim persistente também protege redelivery da tarefa Celery.
    with transaction.atomic():
        config = ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
        tentativa = Tentativa.objects.select_for_update().get(pk=tentativa_id)
        if tentativa.status != Tentativa.Status.RESERVADO or tentativa.chamada_iniciada_em:
            return
        if Contato.objects.filter(pk=tentativa.contato_id, excluido_em__isnull=False).exists():
            finalizar(tentativa, Tentativa.Status.PAUSADO, 'Paciente excluído antes da validação. Nenhum envio iniciado.')
            return
        if not tentativa.manual and not config.ativo:
            finalizar(tentativa, Tentativa.Status.PAUSADO, 'Fila pausada antes da validação. Retomada somente manual.')
            return
        tentativa.chamada_iniciada_em = timezone.now()
        tentativa.save(update_fields=['chamada_iniciada_em'])
    try:
        client = client or EvolutionClient()
        candidatos = variacoes(tentativa.telefone_usado)
        resultado = client.verificar_numeros(tentativa.instancia, candidatos)
        if not isinstance(resultado, list):
            raise ValueError('Resposta inválida na validação.')
        encontrados = set()
        for item in resultado:
            if not isinstance(item, dict) or item.get('exists') is not True:
                continue
            jid = str(item.get('jid') or '')
            numero = normalizar(jid.split('@')[0]) if jid.endswith('@s.whatsapp.net') else ''
            consultado = normalizar(item.get('number'))
            if consultado in candidatos and numero and chave(numero) == chave(tentativa.telefone_usado):
                encontrados.add(numero)
        if len(encontrados) != 1:
            finalizar(tentativa, Tentativa.Status.FALHA, 'Número inexistente ou validação ambígua. Revisão e nova tentativa somente manuais.')
            return
        numero = encontrados.pop()
    except Exception:
        finalizar(tentativa, Tentativa.Status.FALHA, 'Falha ao consultar a Evolution. Nenhum envio iniciado; nova tentativa somente manual.')
        logger.warning('Falha de validação no disparo %s. Sem repetição automática.', tentativa.pk)
        return
    with transaction.atomic():
        config = ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
        dono = NumeroReservado.objects.filter(chave=chave(numero)).first()
        if Contato.objects.filter(pk=tentativa.contato_id, excluido_em__isnull=False).exists():
            finalizar(tentativa, Tentativa.Status.PAUSADO, 'Paciente excluído antes do envio.')
            return
        if dono and dono.contato_id != tentativa.contato_id:
            finalizar(tentativa, Tentativa.Status.BLOQUEADO, 'Número validado já reservado para outro paciente.')
            return
        if not dono:
            NumeroReservado.objects.create(chave=chave(numero), contato_id=tentativa.contato_id)
        if not tentativa.manual and not config.ativo:
            finalizar(tentativa, Tentativa.Status.PAUSADO, 'Fila pausada. Nenhum envio iniciado; retomada somente manual.')
            return
        Tentativa.objects.filter(pk=tentativa.pk).update(telefone_usado=numero,
            status=Tentativa.Status.INCERTO, detalhe='Chamada de envio iniciada; aguarde o resultado.')
    # Uma única chamada de envio. Qualquer erro fica incerto, sem retry de transporte.
    try:
        resposta = client.enviar_texto(tentativa.instancia, numero, tentativa.mensagem)
        externo = (resposta.get('key') or {}).get('id') if isinstance(resposta, dict) else None
        if not externo:
            raise ValueError('Resposta sem ID de mensagem.')
    except Exception:
        finalizar(tentativa, Tentativa.Status.INCERTO, 'Não foi possível confirmar o envio. Confira no celular antes de tentar manualmente.')
        logger.warning('Resultado incerto no disparo %s. Sem repetição automática.', tentativa.pk)
        return
    Tentativa.objects.filter(pk=tentativa.pk).update(id_externo=str(externo)[:150])
    finalizar(tentativa, Tentativa.Status.ENVIADO, 'Mensagem aceita pela Evolution. Isso não comprova leitura nem identidade do destinatário.')
    logger.info('Disparo %s aceito pela Evolution.', tentativa.pk)


def executar_rodada(agora=None, client=None):
    agora = agora or timezone.now()
    config = ConfiguracaoDisparo.obter()
    ConfiguracaoDisparo.objects.filter(pk=1).update(ultima_execucao=agora)
    if not config.ativo:
        return None
    # Um envio por execução; beat a cada minuto respeita intervalo e lote por horário.
    contato = elegiveis(config).first()
    if not contato:
        return None
    tentativa = reservar(contato.pk, agora=agora, agendado=True)
    if tentativa:
        executar_tentativa(tentativa.pk, client=client)
    return tentativa
