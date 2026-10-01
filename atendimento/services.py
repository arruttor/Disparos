"""Regras de atendimento: registro de mensagens e controle robô/humano."""

import logging
from datetime import timedelta

from django.utils import timezone

from integracoes.models import Configuracao

from .models import Conversa, Mensagem, Paciente

logger = logging.getLogger("clinica.atendimento")


def obter_conversa(unidade, telefone, nome=""):
    paciente, criado = Paciente.objects.get_or_create(
        clinica=unidade.clinica, telefone=telefone, defaults={"nome": nome}
    )
    if criado:
        logger.info("Novo paciente pelo WhatsApp: %s (%s)", nome or "sem nome", telefone)
    conversa = (
        Conversa.objects.filter(paciente=paciente, unidade=unidade)
        .exclude(status=Conversa.Status.ENCERRADA)
        .first()
    )
    if conversa is None:
        conversa = Conversa.objects.create(paciente=paciente, unidade=unidade)
    return conversa


def registrar_mensagem(unidade, telefone, texto, id_externo, enviada_por_nos, nome=""):
    """Grava a mensagem. Retorna (conversa, mensagem) ou (None, None) se já registrada."""
    if id_externo and Mensagem.objects.filter(id_externo=id_externo).exists():
        return None, None

    conversa = obter_conversa(unidade, telefone, nome)

    if enviada_por_nos:
        # Mensagem enviada pelo celular/WhatsApp Web = um humano assumiu a conversa.
        # (Mensagens do próprio robô são gravadas com id_externo no envio e caem no "já registrada" acima.)
        origem = Mensagem.Origem.HUMANO
        pausa = Configuracao.obter().pausa_robo_minutos
        conversa.robo_pausado_ate = timezone.now() + timedelta(minutes=pausa)
        conversa.save(update_fields=["robo_pausado_ate", "atualizada_em"])
    else:
        origem = Mensagem.Origem.PACIENTE

    mensagem = Mensagem.objects.create(conversa=conversa, origem=origem, texto=texto, id_externo=id_externo or "")
    return conversa, mensagem


def robo_pode_responder(conversa):
    if conversa.status != Conversa.Status.ROBO:
        return False
    if conversa.robo_pausado_ate and conversa.robo_pausado_ate > timezone.now():
        return False
    whatsapp = getattr(conversa.unidade, "whatsapp", None)
    return bool(whatsapp and whatsapp.robo_ativo)
