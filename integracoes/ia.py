"""Verificação da conexão com o provedor de IA (Anthropic)."""

import logging

import requests
from django.utils import timezone

from .models import Configuracao, StatusConexao

logger = logging.getLogger("clinica.ia")


def verificar_ia(config=None):
    config = config or Configuracao.obter()
    if not config.ia_api_key:
        config.ia_status = StatusConexao.NAO_CONFIGURADO
        config.ia_detalhe_status = "API key não informada."
    else:
        try:
            resp = requests.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": config.ia_api_key, "anthropic-version": "2023-06-01"},
                timeout=15,
            )
            if resp.status_code == 200:
                modelos = [m["id"] for m in resp.json().get("data", [])]
                config.ia_status = StatusConexao.CONECTADO
                config.ia_detalhe_status = (
                    # A lista traz IDs completos (com data); o alias é prefixo deles.
                    "Chave válida." if not modelos or any(m.startswith(config.ia_modelo) for m in modelos)
                    else f"Chave válida, mas o modelo '{config.ia_modelo}' não aparece na lista."
                )
            else:
                config.ia_status = StatusConexao.ERRO
                config.ia_detalhe_status = f"Resposta {resp.status_code}: {resp.text[:200]}"
                logger.error("Falha ao validar chave da IA: %s", config.ia_detalhe_status)
        except requests.RequestException as e:
            config.ia_status = StatusConexao.ERRO
            config.ia_detalhe_status = str(e)[:255]
            logger.error("Erro de conexão com a IA: %s", e)
    config.ia_ultima_verificacao = timezone.now()
    config.save()
    return config.ia_status
