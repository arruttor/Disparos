"""Provedores de IA: constantes e verificação da conexão.

- Anthropic (Claude): SDK oficial `anthropic`.
- Gemini e outros "compatíveis com OpenAI" (Groq, OpenRouter…): SDK `openai` apontado para o endpoint do provedor.
"""

import logging

import anthropic
import openai
from django.utils import timezone

from .models import Configuracao, StatusConexao

logger = logging.getLogger("clinica.ia")

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

MODELO_PADRAO = {
    Configuracao.Provedor.ANTHROPIC: "claude-opus-5-5",
    Configuracao.Provedor.GEMINI: "gemini-3.8-flash",
    Configuracao.Provedor.COMPATIVEL: "",
}


# Reserva quando o principal está sobrecarregado (503) ou sem cota (429). No plano gratuito do
# Gemini as cotas são por modelo, então trocar de modelo também resolve cota esgotada.
MODELO_RESERVA_PADRAO = {
    Configuracao.Provedor.GEMINI: "gemini-3.5-flash",
}

# Tentativas automáticas do SDK (espera crescente entre elas; respeita o retry-after do servidor).
TENTATIVAS_API = 4


def modelo_reserva(config):
    reserva = config.ia_modelo_reserva.strip() or MODELO_RESERVA_PADRAO.get(config.ia_provedor, "")
    return "" if reserva == config.ia_modelo else reserva


def base_url(config):
    if config.ia_provedor == Configuracao.Provedor.GEMINI:
        return GEMINI_BASE_URL
    return config.ia_base_url.strip()


def cliente_compativel(config, timeout=90.0):
    """Cliente OpenAI apontado para o provedor configurado (Gemini, Groq…)."""
    return openai.OpenAI(
        api_key=config.ia_api_key, base_url=base_url(config), timeout=timeout, max_retries=TENTATIVAS_API
    )


def _modelos_anthropic(config):
    cliente = anthropic.Anthropic(api_key=config.ia_api_key, timeout=15.0, max_retries=1)
    return [m.id for m in cliente.models.list()]


def _modelos_compativel(config):
    # O Gemini devolve ids como "models/gemini-3.8-flash"; os demais, sem prefixo.
    return [m.id.removeprefix("models/") for m in cliente_compativel(config, timeout=15.0).models.list()]


def descrever_erro(e):
    """Mensagem curta e útil a partir de um erro de qualquer um dos SDKs."""
    for lib in (anthropic, openai):
        if isinstance(e, lib.AuthenticationError):
            return "API key inválida."
        if isinstance(e, lib.PermissionDeniedError):
            return "A API key não tem permissão para este modelo."
        if isinstance(e, lib.NotFoundError):
            return "Modelo ou endereço não encontrado — confira o nome do modelo em Integrações."
        if isinstance(e, lib.RateLimitError):
            return "Limite de uso atingido (no plano gratuito, espere o limite por minuto/dia renovar)."
        if isinstance(e, lib.BadRequestError):
            return f"Requisição recusada pela API: {getattr(e, 'message', e)}"
        if isinstance(e, lib.APIStatusError) and e.status_code in (503, 529):
            return "O modelo de IA está sobrecarregado no momento (erro do provedor, temporário)."
        if isinstance(e, lib.APIStatusError):
            return f"Erro {e.status_code} na API do provedor."
        if isinstance(e, lib.APIConnectionError):
            return "Sem conexão com a API do provedor."
    return str(e)


def verificar_ia(config=None):
    config = config or Configuracao.obter()
    if not config.ia_api_key:
        config.ia_status = StatusConexao.NAO_CONFIGURADO
        config.ia_detalhe_status = "API key não informada."
    elif config.ia_provedor == Configuracao.Provedor.COMPATIVEL and not config.ia_base_url.strip():
        config.ia_status = StatusConexao.NAO_CONFIGURADO
        config.ia_detalhe_status = "Informe a URL da API do provedor."
    else:
        try:
            if config.ia_provedor == Configuracao.Provedor.ANTHROPIC:
                modelos = _modelos_anthropic(config)
                # A lista pode trazer IDs completos (com data); o alias é prefixo deles.
                encontrado = any(m.startswith(config.ia_modelo) for m in modelos)
            else:
                modelos = _modelos_compativel(config)
                encontrado = config.ia_modelo in modelos
            config.ia_status = StatusConexao.CONECTADO
            config.ia_detalhe_status = (
                f"Chave válida ({config.get_ia_provedor_display()})." if encontrado or not modelos
                else f"Chave válida, mas o modelo '{config.ia_modelo}' não aparece na lista do provedor."
            )
        except (anthropic.APIError, openai.APIError) as e:
            config.ia_status = StatusConexao.ERRO
            config.ia_detalhe_status = descrever_erro(e)[:255]
            logger.error("Falha ao validar a IA (%s): %s", config.get_ia_provedor_display(), config.ia_detalhe_status)
    config.ia_ultima_verificacao = timezone.now()
    config.save()
    return config.ia_status
