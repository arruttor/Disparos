"""Regras de atendimento: registro de mensagens, controle robô/humano e disparo do assistente."""

import logging
import threading
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from integracoes.models import Configuracao

from .models import Conversa, Mensagem, Paciente

logger = logging.getLogger("clinica.atendimento")

# Marca temporária em Mensagem.id_externo enquanto o envio pela Evolution não devolve o id real.
PREFIXO_PENDENTE = "pendente:"
# Janela para reconhecer o "eco" (fromMe) de uma mensagem que o próprio robô acabou de enviar.
JANELA_ECO = timedelta(minutes=2)
TEXTO_TRANSFERENCIA = "Vou pedir para alguém da nossa equipe continuar seu atendimento por aqui, tudo bem? 🙂"


def normalizar_telefone(valor):
    """'(81) 99999-0000' → '5581999990000'. Retorna '' se não parecer um celular/telefone BR."""
    digitos = "".join(c for c in (valor or "") if c.isdigit())
    if len(digitos) in (10, 11):  # DDD + número, sem DDI
        digitos = "55" + digitos
    if len(digitos) not in (12, 13) or not digitos.startswith("55"):
        return ""
    ddd, numero = digitos[2:4], digitos[4:]
    if "0" in ddd:  # DDDs vão de 11 a 99, sem zero
        return ""
    # Celular: 9 dígitos começando com 9. 8 dígitos: fixo, ou celular sem o 9 extra
    # (formato que o WhatsApp ainda usa para alguns números antigos).
    if len(numero) == 9 and numero[0] != "9":
        return ""
    if len(numero) == 8 and numero[0] in "01":
        return ""
    return digitos


def obter_conversa(unidade, telefone, nome=""):
    nome = (nome or "").strip()[:150]
    paciente, criado = Paciente.objects.get_or_create(
        clinica=unidade.clinica, telefone=telefone, defaults={"nome": nome}
    )
    if criado:
        logger.info("Novo paciente pelo WhatsApp: %s (%s)", nome or "sem nome", telefone)
    conversa = (
        Conversa.objects.filter(paciente=paciente, unidade=unidade, simulada=False)
        .exclude(status=Conversa.Status.ENCERRADA)
        .first()
    )
    if conversa is None:
        conversa = Conversa.objects.create(paciente=paciente, unidade=unidade)
    return conversa


def _eco_do_robo(conversa, texto, id_externo):
    """A mensagem fromMe é uma que o sistema acabou de enviar (assistente ou equipe pelo painel)?

    Se for, guarda o id real e diz que sim — para não registrar em dobro nem pausar o assistente.
    """
    pendente = (
        Mensagem.objects.filter(
            conversa=conversa, origem__in=[Mensagem.Origem.ROBO, Mensagem.Origem.HUMANO], texto=texto,
            id_externo__startswith=PREFIXO_PENDENTE, criada_em__gte=timezone.now() - JANELA_ECO,
        )
        .order_by("criada_em")
        .first()
    )
    if pendente is None:
        return False
    if id_externo:
        pendente.id_externo = id_externo
        pendente.save(update_fields=["id_externo"])
    return True


def registrar_mensagem(unidade, telefone, texto, id_externo, enviada_por_nos, nome=""):
    """Grava a mensagem. Retorna (conversa, mensagem) ou (None, None) se já registrada."""
    if id_externo and Mensagem.objects.filter(id_externo=id_externo).exists():
        return None, None

    conversa = obter_conversa(unidade, telefone, nome)

    if enviada_por_nos:
        # O webhook avisa também das mensagens que o próprio robô enviou. Às vezes o aviso chega
        # antes de o envio devolver o id — reconhece pelo texto para não confundir com a equipe.
        if _eco_do_robo(conversa, texto, id_externo):
            return None, None
        # Enviada pelo celular/WhatsApp Web = um humano assumiu a conversa.
        origem = Mensagem.Origem.HUMANO
        pausa = Configuracao.obter().pausa_robo_minutos
        conversa.robo_pausado_ate = timezone.now() + timedelta(minutes=pausa)
        conversa.save(update_fields=["robo_pausado_ate", "atualizada_em"])
    else:
        origem = Mensagem.Origem.PACIENTE

    mensagem = Mensagem.objects.create(conversa=conversa, origem=origem, texto=texto, id_externo=id_externo or "")
    return conversa, mensagem


def robo_pode_responder(conversa):
    if conversa.simulada:
        return conversa.status == Conversa.Status.ROBO
    if conversa.status != Conversa.Status.ROBO:
        return False
    if conversa.robo_pausado_ate and conversa.robo_pausado_ate > timezone.now():
        return False
    # Não exige status "conectado": esse status pode ficar desatualizado (webhook perdido).
    # Se o WhatsApp estiver mesmo fora, o envio falha e vai para os Logs.
    whatsapp = getattr(conversa.unidade, "whatsapp", None)
    return bool(whatsapp and whatsapp.robo_ativo)


# ---------------------------------------------------------------------------
# Envio e transferência
# ---------------------------------------------------------------------------


def _enviar(conversa, texto, origem, autor=None, levantar_erro=False):
    """Grava a mensagem e envia pelo WhatsApp da unidade (conversas simuladas só gravam).

    Grava ANTES de enviar, com id "pendente:…": o eco do webhook pode chegar antes do retorno do envio.
    Falha no envio: a mensagem fica com id "falha:…" (o painel mostra "não enviada").
    """
    from integracoes.evolution import ErroEvolution, EvolutionClient

    if conversa.simulada:
        return Mensagem.objects.create(conversa=conversa, origem=origem, texto=texto, autor=autor)

    mensagem = Mensagem.objects.create(
        conversa=conversa, origem=origem, texto=texto, autor=autor,
        id_externo=f"{PREFIXO_PENDENTE}{uuid.uuid4().hex}",
    )
    instancia = getattr(conversa.unidade, "whatsapp", None)
    try:
        if instancia is None:
            raise ErroEvolution("A unidade não tem WhatsApp configurado.")
        retorno = EvolutionClient().enviar_texto(instancia.nome_instancia, conversa.paciente.telefone, texto)
    except ErroEvolution as e:
        quem = "da equipe" if origem == Mensagem.Origem.HUMANO else "do assistente"
        logger.error("Falha ao enviar mensagem %s (conversa %s): %s", quem, conversa.pk, e)
        mensagem.id_externo = f"falha:{mensagem.id_externo.removeprefix(PREFIXO_PENDENTE)}"
        mensagem.save(update_fields=["id_externo"])
        if levantar_erro:
            raise
        return mensagem
    id_real = (retorno.get("key") or {}).get("id", "")
    mensagem.refresh_from_db(fields=["id_externo"])
    # Se o eco do webhook chegou antes, ele já gravou o id real — não sobrescreve.
    if id_real and mensagem.id_externo.startswith(PREFIXO_PENDENTE):
        mensagem.id_externo = id_real
        mensagem.save(update_fields=["id_externo"])
    return mensagem


def enviar_resposta(conversa, texto):
    """Resposta do assistente."""
    return _enviar(conversa, texto, Mensagem.Origem.ROBO)


# ---------------------------------------------------------------------------
# Caixa de entrada (equipe)
# ---------------------------------------------------------------------------


def assumir_conversa(conversa, usuario, motivo=""):
    """A equipe assume: o assistente para de responder nesta conversa até ela ser devolvida."""
    conversa.status = Conversa.Status.HUMANO
    conversa.atendente = usuario
    if motivo or not conversa.motivo_transferencia:
        conversa.motivo_transferencia = (motivo or f"Assumida por {usuario}")[:255]
    conversa.save(update_fields=["status", "atendente", "motivo_transferencia", "atualizada_em"])


def enviar_mensagem_equipe(conversa, texto, usuario):
    """Resposta da equipe pelo painel. Quem responde assume a conversa. Lança ErroEvolution se não enviar."""
    if conversa.status != Conversa.Status.HUMANO or conversa.atendente_id != usuario.pk:
        assumir_conversa(conversa, usuario)
    return _enviar(conversa, texto, Mensagem.Origem.HUMANO, autor=usuario, levantar_erro=True)


def registrar_nota(conversa, texto, usuario):
    return Mensagem.objects.create(conversa=conversa, origem=Mensagem.Origem.NOTA, texto=texto, autor=usuario)


def devolver_ao_assistente(conversa, usuario=None):
    conversa.status = Conversa.Status.ROBO
    conversa.atendente = None
    conversa.robo_pausado_ate = None
    conversa.motivo_transferencia = ""
    conversa.save(update_fields=["status", "atendente", "robo_pausado_ate", "motivo_transferencia", "atualizada_em"])
    logger.info("Conversa %s (%s) devolvida ao assistente%s.", conversa.pk, conversa.paciente,
                f" por {usuario}" if usuario else "")


def encerrar_conversa(conversa, usuario=None):
    """Encerra: a próxima mensagem do paciente abre uma conversa nova, com o assistente."""
    conversa.status = Conversa.Status.ENCERRADA
    conversa.save(update_fields=["status", "atualizada_em"])
    logger.info("Conversa %s (%s) encerrada%s.", conversa.pk, conversa.paciente, f" por {usuario}" if usuario else "")


def transferir_para_humano(conversa, motivo, resumo=""):
    """Passa a conversa para a equipe e avisa no WhatsApp de alertas da unidade (se cadastrado)."""
    from integracoes.evolution import ErroEvolution, EvolutionClient

    conversa.status = Conversa.Status.HUMANO
    conversa.motivo_transferencia = (motivo or "não informado")[:255]
    conversa.save(update_fields=["status", "motivo_transferencia", "atualizada_em"])
    paciente = conversa.paciente
    logger.info("Conversa %s (%s) transferida para a equipe: %s", conversa.pk, paciente, conversa.motivo_transferencia)

    unidade = conversa.unidade
    alerta = normalizar_telefone(unidade.telefone_alerta)
    instancia = getattr(unidade, "whatsapp", None)
    if conversa.simulada or not alerta or instancia is None:
        return
    texto = (
        f"🔔 *Atendimento para a equipe* — {unidade.nome}\n"
        f"Paciente: {paciente.nome or 'sem nome'} (+{paciente.telefone})\n"
        f"Motivo: {conversa.motivo_transferencia}"
        + (f"\nResumo: {resumo}" if resumo else "")
    )
    try:
        EvolutionClient().enviar_texto(instancia.nome_instancia, alerta, texto)
    except ErroEvolution as e:
        logger.error("Falha ao enviar alerta de transferência (%s): %s", unidade, e)


# ---------------------------------------------------------------------------
# Disparo do assistente
# ---------------------------------------------------------------------------

# Um turno por conversa de cada vez (dentro deste processo). Em produção com vários
# processos isso precisa virar fila (Celery) — ver CLAUDE.md.
_travas = {}
_travas_lock = threading.Lock()


def _trava_da_conversa(conversa_id):
    with _travas_lock:
        return _travas.setdefault(conversa_id, threading.Lock())


def processar_turno(conversa_id, mensagem_id, esperar=True):
    """Espera o paciente terminar de digitar, gera a resposta e envia."""
    from .assistente import MENSAGEM_FALHA, ErroAssistente, descrever_erro_api, responder

    if esperar:
        time.sleep(Configuracao.obter().espera_resposta_segundos)
    # Chegou outra mensagem do paciente depois desta? O turno dela responde tudo junto.
    ultima = (
        Mensagem.objects.filter(conversa_id=conversa_id, origem=Mensagem.Origem.PACIENTE)
        .order_by("-criada_em").values_list("pk", flat=True).first()
    )
    if ultima != mensagem_id:
        return None

    with _trava_da_conversa(conversa_id):
        conversa = Conversa.objects.select_related("paciente", "unidade__clinica", "unidade__whatsapp").get(pk=conversa_id)
        if not robo_pode_responder(conversa):
            return None
        try:
            resultado = responder(conversa)
        except ErroAssistente as e:
            logger.error("Assistente falhou na conversa %s: %s", conversa_id, e)
            resultado = None
        except Exception as e:  # erros da API (chave, créditos, rede…) e inesperados
            logger.exception("Assistente falhou na conversa %s: %s", conversa_id, descrever_erro_api(e))
            resultado = None

        if resultado is None:
            # Falhou: o paciente não fica sem resposta, e a equipe é avisada.
            transferir_para_humano(conversa, "Falha do assistente (veja os Logs)")
            enviar_resposta(conversa, MENSAGEM_FALHA)
            return None
        texto = resultado.texto or (TEXTO_TRANSFERENCIA if resultado.transferiu else "")
        if texto:
            enviar_resposta(conversa, texto)
        return resultado


def _rodar_em_segundo_plano(conversa_id, mensagem_id):
    try:
        processar_turno(conversa_id, mensagem_id)
    except Exception:
        logger.exception("Erro inesperado no turno do assistente (conversa %s)", conversa_id)
    finally:
        close_old_connections()  # a thread abriu a própria conexão com o banco


# Cache da verificação "tem worker do Celery vivo?" (perguntar a cada mensagem custaria ~0,5 s).
_worker_vivo = {"valor": False, "verificado_em": 0.0}
CACHE_WORKER_SEGUNDOS = 30
_aviso_sem_worker = {"em": -1e9}
INTERVALO_AVISO_SEM_WORKER = 600


def celery_tem_worker():
    """Há algum worker respondendo? Sem worker, tarefa na fila = paciente sem resposta."""
    if time.monotonic() - _worker_vivo["verificado_em"] < CACHE_WORKER_SEGUNDOS:
        return _worker_vivo["valor"]
    from config.celery import app

    try:
        vivo = bool(app.control.ping(timeout=0.5))
    except Exception:
        vivo = False
    _worker_vivo.update(valor=vivo, verificado_em=time.monotonic())
    return vivo


def _agendar_no_celery(conversa, mensagem):
    """Põe o turno na fila, para rodar depois da espera. Devolve False se a fila não estiver disponível."""
    from .tasks import processar_turno as tarefa

    if not celery_tem_worker():
        # Um aviso a cada 10 min basta (antes era um por mensagem e poluía os Logs).
        if time.monotonic() - _aviso_sem_worker["em"] > INTERVALO_AVISO_SEM_WORKER:
            logger.warning("Fila configurada, mas nenhum worker do Celery está rodando; respondendo por thread. "
                           "(Este aviso se repete no máximo a cada 10 minutos.)")
            _aviso_sem_worker["em"] = time.monotonic()
        return False
    try:
        tarefa.apply_async(
            args=(conversa.pk, mensagem.pk), countdown=Configuracao.obter().espera_resposta_segundos,
            retry=False,
        )
        return True
    except Exception as e:  # Redis fora do ar, por exemplo
        logger.warning("Fila de tarefas indisponível (%s); respondendo por thread.", e)
        return False


def disparar_assistente(conversa, mensagem):
    """Agenda a resposta do assistente para a mensagem recebida (não bloqueia o webhook).

    Com Celery configurado, vai para a fila (sobrevive a reinício do servidor); senão, thread.
    """
    if not robo_pode_responder(conversa):
        return
    if not getattr(settings, "ASSISTENTE_EM_SEGUNDO_PLANO", True):
        processar_turno(conversa.pk, mensagem.pk, esperar=False)
        return
    if settings.CELERY_BROKER_URL and _agendar_no_celery(conversa, mensagem):
        return
    threading.Thread(
        target=_rodar_em_segundo_plano, args=(conversa.pk, mensagem.pk), daemon=True,
        name=f"assistente-conversa-{conversa.pk}",
    ).start()
