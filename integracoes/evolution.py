"""Cliente da Evolution API (v2).

Toda a comunicação com o WhatsApp passa por aqui. Se um dia o provedor mudar
(ex.: API oficial da Meta), basta reimplementar estas funções.
"""

import logging

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import Configuracao, StatusConexao

logger = logging.getLogger("clinica.evolution")

EVENTOS_WEBHOOK = ["CONNECTION_UPDATE", "QRCODE_UPDATED"]


class ErroEvolution(Exception):
    pass


class EvolutionClient:
    def __init__(self, config=None, timeout=15):
        config = config or Configuracao.obter()
        if not config.evolution_url or not config.evolution_api_key:
            raise ErroEvolution("URL e API key da Evolution não configuradas.")
        self.base = config.evolution_url.rstrip("/")
        self.headers = {"apikey": config.evolution_api_key, "Content-Type": "application/json"}
        self.timeout = timeout

    def _req(self, metodo, caminho, **kwargs):
        url = f"{self.base}{caminho}"
        try:
            resp = requests.request(metodo, url, headers=self.headers, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise ErroEvolution(f"Não foi possível acessar a Evolution em {self.base}: {e}") from e
        if resp.status_code >= 400:
            raise ErroEvolution(f"Evolution respondeu {resp.status_code} em {caminho}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    # --- Instâncias -------------------------------------------------------

    def listar_instancias(self):
        return self._req("GET", "/instance/fetchInstances")

    def instancia_existe(self, nome):
        # Filtrar por instanceName devolve 404 quando não existe, então listamos todas.
        for item in self.listar_instancias():
            if item.get("name") == nome or (item.get("instance") or {}).get("instanceName") == nome:
                return True
        return False

    def criar_instancia(self, nome, numero=""):
        corpo = {"instanceName": nome, "integration": "WHATSAPP-BAILEYS", "qrcode": True}
        if numero:
            corpo["number"] = numero
        return self._req("POST", "/instance/create", json=corpo)

    def obter_qrcode(self, nome, numero=""):
        """Retorna dict com 'base64' (imagem do QR) e/ou 'pairingCode'.

        O código de pareamento é gerado para um número específico — passe o número
        atual (importante ao trocar o número de uma unidade).
        """
        params = {"number": numero} if numero else None
        return self._req("GET", f"/instance/connect/{nome}", params=params)

    def estado_conexao(self, nome):
        """Retorna 'open', 'connecting' ou 'close'."""
        dados = self._req("GET", f"/instance/connectionState/{nome}")
        return (dados.get("instance") or {}).get("state", "close")

    def desconectar(self, nome):
        return self._req("DELETE", f"/instance/logout/{nome}")

    def configurar_webhook(self, nome, url):
        corpo = {
            "webhook": {
                "enabled": True,
                "url": url,
                "webhookByEvents": False,
                "webhookBase64": False,
                "events": EVENTOS_WEBHOOK,
            }
        }
        return self._req("POST", f"/webhook/set/{nome}", json=corpo)

    # --- Mensagens --------------------------------------------------------

    def enviar_texto(self, nome, numero, texto):
        return self._req("POST", f"/message/sendText/{nome}", json={"number": numero, "text": texto})

    def verificar_numeros(self, nome, numeros):
        """Consulta registro WhatsApp. Não confirma nome nem titularidade."""
        return self._req("POST", f"/chat/whatsappNumbers/{nome}", json={"numbers": numeros})


def url_webhook():
    return f"{settings.URL_PUBLICA}{reverse('integracoes:webhook_evolution')}?token={settings.WEBHOOK_TOKEN}"


MAPA_ESTADOS = {
    "open": StatusConexao.CONECTADO,
    "connecting": StatusConexao.AGUARDANDO,
    "close": StatusConexao.DESCONECTADO,
}


def atualizar_status(instancia, client=None):
    """Consulta a Evolution e grava o status na instância. Retorna o status."""
    try:
        client = client or EvolutionClient()
        estado = client.estado_conexao(instancia.nome_instancia)
        instancia.status = MAPA_ESTADOS.get(estado, StatusConexao.DESCONECTADO)
        instancia.detalhe_status = f"Estado na Evolution: {estado}"
    except ErroEvolution as e:
        instancia.status = StatusConexao.ERRO
        instancia.detalhe_status = str(e)[:255]
        logger.error("Falha ao verificar WhatsApp da unidade %s: %s", instancia.unidade, e)
    instancia.ultima_verificacao = timezone.now()
    instancia.save(update_fields=["status", "detalhe_status", "ultima_verificacao"])
    return instancia.status
