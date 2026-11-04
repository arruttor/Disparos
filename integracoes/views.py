import json
import logging
import secrets

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from atendimento.lembretes import tratar_resposta_confirmacao
from atendimento.models import Mensagem
from atendimento.services import disparar_assistente, registrar_mensagem

from .evolution import MAPA_ESTADOS
from .models import InstanciaWhatsApp

logger = logging.getLogger("clinica.webhook")


# Tipos que não são mensagem de verdade: não viram Mensagem nem acionam o assistente.
# (albumMessage só anuncia as fotos, que chegam em seguida, cada uma como imageMessage.)
TIPOS_IGNORADOS = {
    "reactionMessage", "stickerMessage", "protocolMessage", "albumMessage", "secretEncryptedMessage",
    "senderKeyDistributionMessage", "pollUpdateMessage", "keepInChatMessage", "pinInChatMessage",
    "editedMessage", "messageContextInfo",
}

# Mídias: descrição legível para a equipe e para a IA (a legenda, se houver, vem junto).
DESCRICAO_MIDIA = {
    "imageMessage": "📷 Imagem",
    "videoMessage": "🎥 Vídeo",
    "audioMessage": "🎤 Áudio",
    "documentMessage": "📄 Documento",
    "documentWithCaptionMessage": "📄 Documento",
    "locationMessage": "📍 Localização",
    "liveLocationMessage": "📍 Localização em tempo real",
    "contactMessage": "👤 Contato",
    "contactsArrayMessage": "👤 Contatos",
}


def _extrair_texto(message, tipo=""):
    """Texto legível da mensagem, ou "" se ela deve ser ignorada."""
    if not message or tipo in TIPOS_IGNORADOS:
        return ""
    texto = message.get("conversation") or (message.get("extendedTextMessage") or {}).get("text")
    if texto:
        return texto
    tipo = tipo if tipo in DESCRICAO_MIDIA else next((t for t in DESCRICAO_MIDIA if t in message), "")
    if not tipo:
        return ""
    conteudo = message.get(tipo) or {}
    if tipo == "documentWithCaptionMessage":  # documento com legenda vem embrulhado
        conteudo = ((conteudo.get("message") or {}).get("documentMessage")) or {}
    partes = [DESCRICAO_MIDIA[tipo]]
    if conteudo.get("fileName"):
        partes[0] += f": {conteudo['fileName']}"
    if conteudo.get("caption"):
        partes.append(conteudo["caption"])
    return "\n".join(partes)


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
            novo_status = MAPA_ESTADOS.get(estado, instancia.status)
            # A Evolution manda rajadas de "connecting" ao reconectar. Só grava (e loga) quando muda de
            # fato: cada gravação a mais disputa o banco com o painel e o assistente.
            if novo_status != instancia.status:
                logger.info("WhatsApp da unidade %s mudou para '%s'.", instancia.unidade, estado)
                instancia.status = novo_status
                instancia.detalhe_status = f"Estado na Evolution: {estado}"
                instancia.ultima_verificacao = timezone.now()
                instancia.save(update_fields=["status", "detalhe_status", "ultima_verificacao"])

        elif evento == "messages.upsert":
            if settings.MODO_DISPAROS:
                return JsonResponse({"ok": True})
            chave = dados.get("key") or {}
            jid = chave.get("remoteJid", "")
            if not jid.endswith("@s.whatsapp.net"):
                return JsonResponse({"ok": True})  # ignora grupos, status, etc.
            tipo = dados.get("messageType", "")
            if tipo in TIPOS_IGNORADOS:
                return JsonResponse({"ok": True})  # reação, figurinha, apagar/editar… não é mensagem
            texto = _extrair_texto(dados.get("message"), tipo)
            if not texto:
                # Tipo que ainda não conhecemos: registra para a equipe ver (e para sabermos que existe).
                logger.info("Mensagem de tipo não tratado recebida: %s", tipo or "desconhecido")
                texto = f"❔ Mensagem sem texto ({tipo or 'tipo desconhecido'})"
            conversa, mensagem = registrar_mensagem(
                unidade=instancia.unidade,
                telefone=jid.split("@")[0],
                texto=texto,
                id_externo=chave.get("id", ""),
                enviada_por_nos=bool(chave.get("fromMe")),
                # pushName pode vir null (contato sem nome no perfil) — `or ""` evita gravar None.
                nome=(dados.get("pushName") or "") if not chave.get("fromMe") else "",
            )
            if mensagem is not None and mensagem.origem == Mensagem.Origem.PACIENTE:
                # "SIM" a um pedido de confirmação é resolvido direto (sem IA, mesmo com o assistente desligado).
                if not tratar_resposta_confirmacao(conversa, mensagem):
                    disparar_assistente(conversa, mensagem)
    except Exception:
        logger.exception("Erro ao processar webhook '%s' da instância %s", evento, instancia.nome_instancia)

    return JsonResponse({"ok": True})
