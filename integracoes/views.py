import json
import logging
import secrets

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from atendimento.services import registrar_mensagem

from .evolution import MAPA_ESTADOS
from .models import InstanciaWhatsApp

logger = logging.getLogger("clinica.webhook")


def _extrair_texto(message):
    if not message:
        return ""
    return (
        message.get("conversation")
        or (message.get("extendedTextMessage") or {}).get("text")
        or (message.get("imageMessage") or {}).get("caption")
        or ""
    )


@csrf_exempt
@require_POST
def webhook_evolution(request):
    if not secrets.compare_digest(request.GET.get("token", ""), settings.WEBHOOK_TOKEN):
        logger.warning("Webhook recebido com token inválido (IP %s).", request.META.get("REMOTE_ADDR"))
        return HttpResponseForbidden()

    try:
        corpo = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    evento = (corpo.get("event") or "").lower().replace("_", ".")
    instancia = InstanciaWhatsApp.objects.select_related("unidade__clinica").filter(
        nome_instancia=corpo.get("instance", "")
    ).first()
    if instancia is None:
        logger.warning("Webhook de instância desconhecida: %s", corpo.get("instance"))
        return JsonResponse({"ok": True})

    dados = corpo.get("data") or {}

    try:
        if evento == "connection.update":
            estado = dados.get("state", "")
            instancia.status = MAPA_ESTADOS.get(estado, instancia.status)
            instancia.detalhe_status = f"Estado na Evolution: {estado}"
            instancia.ultima_verificacao = timezone.now()
            instancia.save(update_fields=["status", "detalhe_status", "ultima_verificacao"])
            logger.info("WhatsApp da unidade %s mudou para '%s'.", instancia.unidade, estado)

        elif evento == "messages.upsert":
            chave = dados.get("key") or {}
            jid = chave.get("remoteJid", "")
            if not jid.endswith("@s.whatsapp.net"):
                return JsonResponse({"ok": True})  # ignora grupos, status, etc.
            texto = _extrair_texto(dados.get("message"))
            if not texto:
                texto = f"[{dados.get('messageType', 'mídia')}]"
            registrar_mensagem(
                unidade=instancia.unidade,
                telefone=jid.split("@")[0],
                texto=texto,
                id_externo=chave.get("id", ""),
                enviada_por_nos=bool(chave.get("fromMe")),
                nome=dados.get("pushName", "") if not chave.get("fromMe") else "",
            )
            # Próxima etapa: se robo_pode_responder(conversa), acionar a IA.
    except Exception:
        logger.exception("Erro ao processar webhook '%s' da instância %s", evento, instancia.nome_instancia)

    return JsonResponse({"ok": True})
