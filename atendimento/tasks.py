"""Tarefas do Celery (só usadas quando CELERY_BROKER_URL está configurado)."""

import logging

from celery import shared_task

logger = logging.getLogger("clinica.tarefas")


@shared_task(name="atendimento.tarefas.processar_turno", ignore_result=True)
def processar_turno(conversa_id, mensagem_id):
    """Turno do assistente. A espera antes de responder é feita pelo `countdown` do agendamento da tarefa."""
    from .services import processar_turno as executar

    executar(conversa_id, mensagem_id, esperar=False)


@shared_task(name="atendimento.tarefas.enviar_lembretes", ignore_result=True)
def enviar_lembretes():
    from .lembretes import executar

    resultado = executar()
    if resultado.confirmacoes or resultado.lembretes or resultado.falhas:
        logger.info("Rodada de lembretes: %d confirmação(ões), %d lembrete(s), %d falha(s).",
                    resultado.confirmacoes, resultado.lembretes, resultado.falhas)
