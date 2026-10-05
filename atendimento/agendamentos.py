"""Marcar, confirmar, remarcar e cancelar consultas — sempre em sincronia com o Google Agenda.

Regra: o sistema e o Google nunca divergem por falha nossa. Cada operação grava
no banco e no Google dentro da mesma transação; se o Google falhar, nada é gravado.
Estas funções são as que a IA vai chamar.
"""

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from integracoes.google import ErroEventoNaoEncontrado, ErroGoogle, GoogleAgenda

from .agenda import buscar_horarios_livres
from .models import Agendamento

logger = logging.getLogger("clinica.agendamento")

# Como cada status aparece no Google Agenda.
# Cores: https://developers.google.com/calendar/api/v3/reference/colors (5 = banana, 10 = manjericão, 11 = tomate)
VISUAL = {
    Agendamento.Status.AGUARDANDO: {"prefixo": "⏳", "cor": "5"},
    Agendamento.Status.CONFIRMADO: {"prefixo": "✅", "cor": "10"},
    Agendamento.Status.CANCELADO: {"prefixo": "❌", "cor": "11"},
}


class ErroAgendamento(Exception):
    """Erro de regra de negócio, com mensagem pronta para mostrar ao usuário/paciente."""


def corpo_evento(ag):
    """Representação do agendamento como evento do Google Agenda."""
    visual = VISUAL[ag.status]
    paciente = ag.paciente
    linhas = [
        f"Paciente: {paciente.nome or 'sem nome'}",
        f"Telefone: +{paciente.telefone}",
        f"Procedimento: {ag.procedimento.nome} ({ag.procedimento.duracao_minutos} min)",
        f"Status: {ag.get_status_display()}",
        f"Marcado por: {ag.get_origem_display()}",
    ]
    if ag.observacoes:
        linhas.append(f"Observações: {ag.observacoes}")
    if ag.status == Agendamento.Status.CANCELADO and ag.motivo_cancelamento:
        linhas.append(f"Motivo do cancelamento: {ag.motivo_cancelamento}")
    linhas.append("")
    linhas.append("Criado pelo sistema da clínica. Para alterar, use o painel ou o WhatsApp — "
                  "mudanças feitas direto aqui não voltam para o sistema.")
    return {
        "summary": f"{visual['prefixo']} {paciente.nome or paciente.telefone} — {ag.procedimento.nome}",
        "description": "\n".join(linhas),
        "start": {"dateTime": timezone.localtime(ag.inicio).isoformat(), "timeZone": timezone.get_current_timezone_name()},
        "end": {"dateTime": timezone.localtime(ag.fim).isoformat(), "timeZone": timezone.get_current_timezone_name()},
        "colorId": visual["cor"],
        # Cancelado continua visível (histórico para a equipe), mas não ocupa o horário.
        "transparency": "transparent" if ag.status == Agendamento.Status.CANCELADO else "opaque",
        "extendedProperties": {"private": {"agendamento_id": str(ag.pk), "status": ag.status}},
    }


def _agenda(ag):
    conta = getattr(ag.dentista.unidade, "conta_google", None)
    if conta is None or not conta.token_json:
        raise ErroAgendamento(f"A unidade {ag.dentista.unidade} não tem Google Agenda conectado.")
    return GoogleAgenda(conta)


def _confere_disponivel(dentista, procedimento, inicio, ignorar=None):
    try:
        livres = buscar_horarios_livres(
            dentista, procedimento, timezone.localdate(inicio), dias=1, ignorar=ignorar
        )
    except ErroGoogle as e:
        raise ErroAgendamento(f"Não foi possível conferir a agenda agora: {e}") from e
    if inicio not in livres.get(timezone.localdate(inicio), []):
        raise ErroAgendamento("Esse horário não está mais disponível. Escolha outro.")


def marcar(paciente, dentista, procedimento, inicio, origem=Agendamento.Origem.PAINEL, observacoes=""):
    """Marca a consulta: confere a agenda, cria o registro e o evento no Google."""
    _confere_disponivel(dentista, procedimento, inicio)
    with transaction.atomic():
        ag = Agendamento.objects.create(
            paciente=paciente,
            dentista=dentista,
            procedimento=procedimento,
            inicio=inicio,
            fim=inicio + timedelta(minutes=procedimento.duracao_minutos),
            origem=origem,
            observacoes=observacoes,
            google_calendar_id=dentista.google_calendar_id or "primary",
        )
        try:
            evento = _agenda(ag).criar_evento(ag.google_calendar_id, corpo_evento(ag))
        except ErroGoogle as e:
            logger.error("Falha ao criar evento no Google para %s: %s", ag, e)
            raise ErroAgendamento(f"Não foi possível marcar no Google Agenda: {e}") from e
        ag.google_event_id = evento.get("id", "")
        ag.save(update_fields=["google_event_id"])
    logger.info("Consulta marcada: %s (%s, origem: %s).", ag, dentista, ag.get_origem_display())
    return ag


def _sincronizar(ag, tolerar_evento_apagado=False):
    """Envia o estado atual do agendamento para o evento do Google."""
    if not ag.google_event_id:
        return
    try:
        _agenda(ag).atualizar_evento(ag.google_calendar_id or "primary", ag.google_event_id, corpo_evento(ag))
    except ErroEventoNaoEncontrado:
        if tolerar_evento_apagado:
            logger.warning("Evento de %s já não existia no Google (apagado direto na agenda).", ag)
            return
        raise ErroAgendamento("O evento desta consulta foi apagado direto no Google Agenda. "
                              "Cancele aqui e marque de novo.")
    except ErroGoogle as e:
        logger.error("Falha ao atualizar evento de %s no Google: %s", ag, e)
        raise ErroAgendamento(f"Não foi possível atualizar o Google Agenda: {e}") from e


def confirmar(ag):
    if ag.status == Agendamento.Status.CANCELADO:
        raise ErroAgendamento("Esta consulta foi cancelada e não pode ser confirmada.")
    if ag.status == Agendamento.Status.CONFIRMADO:
        return ag
    with transaction.atomic():
        ag.status = Agendamento.Status.CONFIRMADO
        ag.confirmado_em = timezone.now()
        ag.save(update_fields=["status", "confirmado_em"])
        _sincronizar(ag)
    logger.info("Consulta confirmada: %s.", ag)
    return ag


def cancelar(ag, motivo=""):
    if ag.status == Agendamento.Status.CANCELADO:
        return ag
    with transaction.atomic():
        ag.status = Agendamento.Status.CANCELADO
        ag.cancelado_em = timezone.now()
        ag.motivo_cancelamento = motivo[:255]
        ag.save(update_fields=["status", "cancelado_em", "motivo_cancelamento"])
        # Se alguém apagou o evento direto no Google, cancelar aqui continua valendo.
        _sincronizar(ag, tolerar_evento_apagado=True)
    logger.info("Consulta cancelada: %s. Motivo: %s", ag, motivo or "não informado")
    return ag


def remarcar(ag, novo_inicio):
    if ag.status == Agendamento.Status.CANCELADO:
        raise ErroAgendamento("Esta consulta foi cancelada. Marque uma nova.")
    _confere_disponivel(ag.dentista, ag.procedimento, novo_inicio, ignorar=ag)
    antigo = ag.inicio
    with transaction.atomic():
        ag.inicio = novo_inicio
        ag.fim = novo_inicio + timedelta(minutes=ag.procedimento.duracao_minutos)
        # Horário novo = precisa de nova confirmação e novo lembrete.
        ag.status = Agendamento.Status.AGUARDANDO
        ag.confirmado_em = None
        ag.lembrete_enviado_em = None
        ag.save(update_fields=["inicio", "fim", "status", "confirmado_em", "lembrete_enviado_em"])
        _sincronizar(ag)
    logger.info("Consulta remarcada: %s (antes: %s).", ag, timezone.localtime(antigo).strftime("%d/%m %H:%M"))
    return ag
