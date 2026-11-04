"""Confirmação na véspera (D-1), lembrete no dia e tratamento do "sim" do paciente.

`executar()` é chamado periodicamente (Celery beat, comando `enviar_lembretes` ou botão no painel).
É idempotente: cada consulta recebe no máximo um pedido de confirmação e um lembrete.
"""

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import time, timedelta

from django.db.models import F
from django.utils import timezone

from integracoes.models import Configuracao

from . import agendamentos
from .models import Agendamento

logger = logging.getLogger("clinica.lembretes")

# Só manda mensagem automática nesse intervalo (horário de Brasília).
JANELA_ENVIO = (time(8, 0), time(20, 0))
# Consulta marcada há menos que isso não recebe pedido de confirmação (acabou de marcar).
CARENCIA_CONFIRMACAO = timedelta(hours=2)
# Por quanto tempo um "sim" do paciente vale como resposta ao pedido de confirmação.
VALIDADE_PEDIDO = timedelta(hours=36)

DIAS_SEMANA = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"]

RESPOSTAS_SIM = {
    "sim", "s", "ss", "sim sim", "simm", "confirmo", "confirmado", "confirmada", "confirmar", "pode confirmar",
    "sim confirmo", "sim pode confirmar", "ok", "okay", "1", "certo", "combinado", "beleza", "sim obrigado",
    "sim obrigada", "confirmadissimo", "👍", "✅",
}
# Se aparecer algo assim, não é um "sim" simples — fica com o assistente/equipe.
PALAVRAS_DE_DUVIDA = ("nao", "mas", "remarc", "mudar", "trocar", "desmarc", "cancel", "outro", "adiar", "?")


@dataclass
class ResultadoEnvio:
    confirmacoes: int = 0
    lembretes: int = 0
    falhas: int = 0
    motivo: str = ""  # por que nada foi enviado (fora da janela, desativado…)
    detalhes: list = field(default_factory=list)


def _hora(dt):
    local = timezone.localtime(dt)
    return f"{local:%H}h" if local.minute == 0 else f"{local:%H}h{local:%M}"


def _primeiro_nome(paciente):
    return (paciente.nome or "").split(" ")[0] or "tudo bem"


def texto_confirmacao(ag):
    local = timezone.localtime(ag.inicio)
    clinica = ag.dentista.unidade.clinica.nome
    return (
        f"Olá, {_primeiro_nome(ag.paciente)}! 😊 Aqui é da {clinica}.\n"
        f"Passando para confirmar sua consulta de *{ag.procedimento.nome}* amanhã, "
        f"*{DIAS_SEMANA[local.weekday()]}, {local:%d/%m}, às {_hora(ag.inicio)}*, com {ag.dentista.nome} "
        f"(unidade {ag.dentista.unidade.nome}).\n\n"
        f"Você confirma? Responda *SIM* para confirmar. Se precisar mudar, é só me dizer que a gente remarca."
    )


def texto_lembrete_dia(ag):
    unidade = ag.dentista.unidade
    endereco = f"\n📍 {unidade.endereco}" if unidade.endereco else ""
    return (
        f"Oi, {_primeiro_nome(ag.paciente)}! Lembrete: sua consulta de *{ag.procedimento.nome}* é *hoje às "
        f"{_hora(ag.inicio)}*, com {ag.dentista.nome}.{endereco}\nAté já! 🦷"
    )


def _telefone_valido(paciente):
    return paciente.telefone.isdigit()  # pacientes do simulador ("sim1") não recebem nada


def _enviar_para_paciente(ag, texto):
    """Envia na conversa do paciente (mensagem do assistente). Devolve True se saiu pelo WhatsApp."""
    from .services import enviar_resposta, obter_conversa

    unidade = ag.dentista.unidade
    if getattr(unidade, "whatsapp", None) is None:
        return False
    conversa = obter_conversa(unidade, ag.paciente.telefone, ag.paciente.nome)
    mensagem = enviar_resposta(conversa, texto)
    # Falha no envio deixa o id como "falha:…" (ver services._enviar).
    return not mensagem.id_externo.startswith("falha:")


def pendentes_confirmacao(agora, config, manual=False):
    """`manual` (botão do painel): ignora a hora configurada e a carência de quem acabou de marcar."""
    if not config.confirmacao_ativa:
        return Agendamento.objects.none()
    if not manual and timezone.localtime(agora).time() < config.confirmacao_hora:
        return Agendamento.objects.none()
    amanha = timezone.localdate(agora) + timedelta(days=1)
    filtros = {"status": Agendamento.Status.AGUARDANDO, "lembrete_enviado_em__isnull": True, "inicio__date": amanha}
    if not manual:
        filtros["criado_em__lte"] = agora - CARENCIA_CONFIRMACAO
    return (
        Agendamento.objects.filter(**filtros)
        .select_related("paciente", "procedimento", "dentista__unidade__clinica", "dentista__unidade__whatsapp")
        .order_by("inicio")
    )


def pendentes_lembrete_dia(agora, config, manual=False):
    if not config.lembrete_horas_antes:
        return Agendamento.objects.none()
    antecedencia = timedelta(hours=config.lembrete_horas_antes)
    filtros = {
        "status__in": [Agendamento.Status.AGUARDANDO, Agendamento.Status.CONFIRMADO],
        "aviso_dia_enviado_em__isnull": True,
        "inicio__gt": agora, "inicio__lte": agora + antecedencia,
    }
    if not manual:
        # Marcada já dentro da janela do lembrete? Acabou de marcar; não precisa lembrar.
        filtros["criado_em__lte"] = F("inicio") - antecedencia
    return (
        Agendamento.objects.filter(**filtros)
        .select_related("paciente", "procedimento", "dentista__unidade__clinica", "dentista__unidade__whatsapp")
        .order_by("inicio")
    )


def executar(agora=None, ignorar_janela=False):
    """Envia o que estiver pendente.

    `ignorar_janela` = envio manual pelo botão do painel: ignora a janela 8h–20h, a hora configurada e as
    carências de "acabou de marcar" (quem clica quer enviar já).
    """
    from django.conf import settings
    if settings.MODO_DISPAROS:
        return ResultadoEnvio(motivo='Lembretes desativados: sistema exclusivo de disparos.')
    agora = agora or timezone.now()
    config = Configuracao.obter()
    resultado = ResultadoEnvio()
    hora_local = timezone.localtime(agora).time()
    if not ignorar_janela and not (JANELA_ENVIO[0] <= hora_local < JANELA_ENVIO[1]):
        resultado.motivo = "Fora do horário de envio (8h às 20h)."
        return resultado

    for ag in pendentes_confirmacao(agora, config, manual=ignorar_janela):
        if not _telefone_valido(ag.paciente):
            continue
        if _enviar_para_paciente(ag, texto_confirmacao(ag)):
            Agendamento.objects.filter(pk=ag.pk).update(lembrete_enviado_em=timezone.now())
            resultado.confirmacoes += 1
            resultado.detalhes.append(f"confirmação: {ag.paciente} ({_hora(ag.inicio)})")
            logger.info("Pedido de confirmação enviado: %s.", ag)
        else:
            resultado.falhas += 1
            logger.error("Não foi possível enviar o pedido de confirmação: %s (tenta de novo na próxima rodada).", ag)

    for ag in pendentes_lembrete_dia(agora, config, manual=ignorar_janela):
        if not _telefone_valido(ag.paciente):
            continue
        if _enviar_para_paciente(ag, texto_lembrete_dia(ag)):
            Agendamento.objects.filter(pk=ag.pk).update(aviso_dia_enviado_em=timezone.now())
            resultado.lembretes += 1
            resultado.detalhes.append(f"lembrete: {ag.paciente} ({_hora(ag.inicio)})")
            logger.info("Lembrete do dia enviado: %s.", ag)
        else:
            resultado.falhas += 1
            logger.error("Não foi possível enviar o lembrete do dia: %s (tenta de novo na próxima rodada).", ag)

    if not (resultado.confirmacoes or resultado.lembretes or resultado.falhas):
        resultado.motivo = "Nenhuma consulta precisando de confirmação ou lembrete agora."
    return resultado


# ---------------------------------------------------------------------------
# Resposta do paciente
# ---------------------------------------------------------------------------


def _normalizar(texto):
    sem_acento = unicodedata.normalize("NFKD", texto.lower()).encode("ascii", "ignore").decode()
    sem_acento = re.sub(r"[^a-z0-9? ]+", " ", sem_acento)
    return re.sub(r"\s+", " ", sem_acento).strip()


def eh_confirmacao(texto):
    """"sim", "confirmo", "ok", "👍"… — e nada que indique dúvida ou mudança."""
    bruto = texto.strip()
    if bruto in RESPOSTAS_SIM:  # emojis ficam de fora da normalização
        return True
    normal = _normalizar(bruto)
    if not normal or len(normal) > 40 or any(p in normal for p in PALAVRAS_DE_DUVIDA):
        return False
    return normal in RESPOSTAS_SIM or normal.startswith(("sim ", "confirmo ", "confirmado "))


def tratar_resposta_confirmacao(conversa, mensagem):
    """Se a mensagem for um "sim" a um pedido de confirmação recente, confirma e responde.

    Devolve True quando tratou (aí o assistente não precisa entrar). Funciona com o assistente desligado.
    """
    from .services import enviar_resposta

    if not eh_confirmacao(mensagem.texto):
        return False
    agora = timezone.now()
    ag = (
        Agendamento.objects.filter(
            paciente=conversa.paciente, status=Agendamento.Status.AGUARDANDO,
            lembrete_enviado_em__gte=agora - VALIDADE_PEDIDO, inicio__gt=agora,
        )
        .select_related("dentista__unidade", "procedimento", "paciente")
        .order_by("inicio")
        .first()
    )
    if ag is None:
        return False
    try:
        agendamentos.confirmar(ag)
    except agendamentos.ErroAgendamento as e:
        logger.error("Paciente respondeu SIM, mas não foi possível confirmar %s: %s", ag, e)
        return False  # deixa o assistente/equipe lidar
    local = timezone.localtime(ag.inicio)
    enviar_resposta(
        conversa,
        f"Consulta confirmada ✅ Te esperamos {DIAS_SEMANA[local.weekday()]}, {local:%d/%m}, às {_hora(ag.inicio)}. Até lá!",
    )
    logger.info("Consulta confirmada pelo paciente via WhatsApp: %s.", ag)
    return True
