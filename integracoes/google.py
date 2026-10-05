"""Cliente do Google (OAuth 2.0 + Google Calendar API v3), via REST.

Toda a comunicação com o Google passa por aqui. Tokens ficam em
ContaGoogle.token_json (criptografado). O access token é renovado sozinho
com o refresh token quando está para vencer.
"""

import json
import logging
import time
from datetime import datetime
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import Configuracao, StatusConexao

logger = logging.getLogger("clinica.google")

URL_AUTORIZACAO = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
URL_REVOGAR = "https://oauth2.googleapis.com/revoke"
URL_USUARIO = "https://openidconnect.googleapis.com/v1/userinfo"
URL_CALENDAR = "https://www.googleapis.com/calendar/v3"

ESCOPOS = ["openid", "email", "https://www.googleapis.com/auth/calendar"]

# Renova o access token se faltar menos que isso para vencer.
MARGEM_RENOVACAO_SEG = 120


class ErroGoogle(Exception):
    pass


class ErroReconectar(ErroGoogle):
    """O Google recusou o refresh token: é preciso conectar a conta de novo."""


class ErroEventoNaoEncontrado(ErroGoogle):
    """O evento não existe mais no Google (apagado direto na agenda)."""


def url_retorno():
    return f"{settings.URL_SITE}{reverse('painel:google_callback')}"


def _credenciais(config=None):
    config = config or Configuracao.obter()
    if not config.google_client_id or not config.google_client_secret:
        raise ErroGoogle("Client ID e Client secret do Google não configurados.")
    return config.google_client_id, config.google_client_secret


def url_autorizacao(state, config=None):
    client_id, _ = _credenciais(config)
    parametros = {
        "client_id": client_id,
        "redirect_uri": url_retorno(),
        "response_type": "code",
        "scope": " ".join(ESCOPOS),
        "access_type": "offline",  # para receber refresh token
        "prompt": "consent",  # garante o refresh token mesmo se a conta já autorizou antes
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{URL_AUTORIZACAO}?{urlencode(parametros)}"


def _post_token(dados):
    try:
        resp = requests.post(URL_TOKEN, data=dados, timeout=15)
    except requests.RequestException as e:
        raise ErroGoogle(f"Não foi possível acessar o Google: {e}") from e
    try:
        corpo = resp.json() if resp.content else {}
    except ValueError:
        corpo = {}
    if resp.status_code >= 400:
        erro = corpo.get("error", "")
        if erro == "invalid_grant":
            raise ErroReconectar("O Google recusou a autorização (revogada ou expirada). Conecte a conta novamente.")
        raise ErroGoogle(f"Google respondeu {resp.status_code}: {corpo.get('error_description') or erro or resp.text[:200]}")
    return corpo


def _montar_token(resposta, refresh_token_anterior=""):
    return {
        "access_token": resposta["access_token"],
        # Na renovação o Google normalmente não reenvia o refresh token.
        "refresh_token": resposta.get("refresh_token") or refresh_token_anterior,
        "expira_em": int(time.time()) + int(resposta.get("expires_in", 3600)),
        "escopo": resposta.get("scope", ""),
    }


def trocar_codigo(codigo, config=None):
    """Troca o código do retorno do OAuth por tokens. Retorna (token_dict, email)."""
    client_id, client_secret = _credenciais(config)
    resposta = _post_token({
        "code": codigo,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": url_retorno(),
        "grant_type": "authorization_code",
    })
    token = _montar_token(resposta)
    if not token["refresh_token"]:
        raise ErroGoogle("O Google não enviou o refresh token. Remova o acesso do app na conta Google e conecte de novo.")
    if "https://www.googleapis.com/auth/calendar" not in token["escopo"].split():
        raise ErroGoogle("A permissão de acesso à agenda não foi concedida. Conecte de novo e marque a opção da agenda.")

    try:
        resp = requests.get(URL_USUARIO, headers={"Authorization": f"Bearer {token['access_token']}"}, timeout=15)
        email = resp.json().get("email", "") if resp.ok else ""
    except requests.RequestException:
        email = ""
    return token, email


def revogar(token_dict):
    alvo = token_dict.get("refresh_token") or token_dict.get("access_token")
    if not alvo:
        return
    try:
        requests.post(URL_REVOGAR, data={"token": alvo}, timeout=10)
    except requests.RequestException as e:
        logger.warning("Falha ao revogar token no Google (ignorada): %s", e)


class GoogleAgenda:
    """Acesso à agenda de uma conta conectada (ContaGoogle)."""

    def __init__(self, conta, config=None, timeout=15):
        if not conta.token_json:
            raise ErroReconectar("Nenhuma conta Google conectada nesta unidade.")
        self.conta = conta
        self.config = config
        self.timeout = timeout
        self.token = json.loads(conta.token_json)

    def _access_token(self):
        if self.token.get("expira_em", 0) - time.time() > MARGEM_RENOVACAO_SEG:
            return self.token["access_token"]
        client_id, client_secret = _credenciais(self.config)
        resposta = _post_token({
            "refresh_token": self.token.get("refresh_token", ""),
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        })
        self.token = _montar_token(resposta, self.token.get("refresh_token", ""))
        self.conta.token_json = json.dumps(self.token)
        self.conta.save(update_fields=["token_json"])
        return self.token["access_token"]

    def _req(self, metodo, caminho, **kwargs):
        headers = {"Authorization": f"Bearer {self._access_token()}"}
        try:
            resp = requests.request(metodo, f"{URL_CALENDAR}{caminho}", headers=headers, timeout=self.timeout, **kwargs)
        except requests.RequestException as e:
            raise ErroGoogle(f"Não foi possível acessar o Google Agenda: {e}") from e
        if resp.status_code == 401:
            raise ErroReconectar("O Google recusou o acesso à agenda. Conecte a conta novamente.")
        if resp.status_code in (404, 410) and "/events" in caminho:
            raise ErroEventoNaoEncontrado("O evento não existe mais no Google Agenda (foi apagado direto na agenda?).")
        if resp.status_code >= 400:
            raise ErroGoogle(f"Google Agenda respondeu {resp.status_code} em {caminho}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    def listar_calendarios(self):
        """Calendários em que a conta pode criar eventos. Lista de dicts {id, nome, principal}."""
        dados = self._req("GET", "/users/me/calendarList", params={"minAccessRole": "writer", "maxResults": 250})
        calendarios = [
            {"id": c["id"], "nome": c.get("summaryOverride") or c.get("summary", c["id"]), "principal": bool(c.get("primary"))}
            for c in dados.get("items", [])
        ]
        return sorted(calendarios, key=lambda c: (not c["principal"], c["nome"].lower()))

    def ocupados(self, calendar_ids, inicio, fim):
        """Intervalos ocupados por calendário: {id: [(inicio, fim), ...]} com datetimes aware."""
        corpo = {
            "timeMin": inicio.isoformat(),
            "timeMax": fim.isoformat(),
            "timeZone": settings.TIME_ZONE,
            "items": [{"id": cid} for cid in calendar_ids],
        }
        dados = self._req("POST", "/freeBusy", json=corpo)
        resultado = {}
        for cid, info in (dados.get("calendars") or {}).items():
            if info.get("errors"):
                motivo = ", ".join(e.get("reason", "?") for e in info["errors"])
                raise ErroGoogle(f"Não foi possível ler o calendário {cid}: {motivo}")
            resultado[cid] = [
                (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"])) for b in info.get("busy", [])
            ]
        return resultado

    def criar_evento(self, calendar_id, corpo):
        """Cria o evento e devolve o recurso criado (com 'id')."""
        return self._req("POST", f"/calendars/{quote(calendar_id, safe='')}/events", json=corpo)

    def atualizar_evento(self, calendar_id, event_id, corpo):
        """Atualiza só os campos enviados (PATCH)."""
        caminho = f"/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        return self._req("PATCH", caminho, json=corpo)


def atualizar_status(conta):
    """Verifica a conexão (renovando o token se preciso) e grava o status. Retorna o status."""
    try:
        GoogleAgenda(conta).listar_calendarios()
        conta.status = StatusConexao.CONECTADO
        conta.detalhe_status = "Acesso à agenda funcionando."
    except ErroReconectar as e:
        conta.status = StatusConexao.DESCONECTADO
        conta.detalhe_status = str(e)[:255]
        logger.error("Google da unidade %s precisa ser reconectado: %s", conta.unidade, e)
    except ErroGoogle as e:
        conta.status = StatusConexao.ERRO
        conta.detalhe_status = str(e)[:255]
        logger.error("Falha ao verificar Google da unidade %s: %s", conta.unidade, e)
    conta.ultima_verificacao = timezone.now()
    conta.save(update_fields=["status", "detalhe_status", "ultima_verificacao"])
    return conta.status
