"""Mantém em Conversa o resumo da última mensagem (para a caixa de entrada não varrer mensagens)."""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .models import Conversa, Mensagem


def resumo_da_mensagem(mensagem):
    return {
        "ultima_mensagem_em": mensagem.criada_em,
        "ultima_mensagem_origem": mensagem.origem,
        "ultima_mensagem_texto": mensagem.texto[:140],
    }


@receiver(post_save, sender=Mensagem)
def atualizar_resumo_da_conversa(sender, instance, created, **kwargs):
    # Notas internas não mudam "quem falou por último" (o paciente continua esperando resposta).
    if not created or instance.origem == Mensagem.Origem.NOTA:
        return
    Conversa.objects.filter(pk=instance.conversa_id).update(**resumo_da_mensagem(instance), atualizada_em=timezone.now())
