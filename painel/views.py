import json
import logging
import secrets
import time
from datetime import date, datetime, timedelta
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from atendimento import agendamentos
from atendimento.agenda import buscar_horarios_livres
from atendimento.models import Agendamento, Conversa, Mensagem, Paciente
from atendimento.services import (
    TEXTO_TRANSFERENCIA,
    assumir_conversa,
    celery_tem_worker,
    devolver_ao_assistente,
    encerrar_conversa,
    enviar_mensagem_equipe,
    enviar_resposta,
    normalizar_telefone,
    registrar_nota,
)
from core.models import Dentista, Procedimento, RegistroLog, Unidade
from integracoes import google
from integracoes.evolution import ErroEvolution, EvolutionClient, atualizar_status, url_webhook
from integracoes.ia import verificar_ia
from integracoes.models import Configuracao, ContaGoogle, InstanciaWhatsApp, StatusConexao

from .forms import FormAssistente, FormEvolution, FormGoogle, FormIA, FormLembretes, FormNumeroWhatsApp

logger = logging.getLogger("clinica.painel")

# Quantas vezes pedir o QR code à Evolution (1 s entre tentativas) antes de mostrar "gerando…".
TENTATIVAS_QRCODE = 4


def superusuario(view):
    @login_required
    @wraps(view)
    def _view(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return _view


@superusuario
def dashboard(request):
    agora = timezone.localtime()
    hoje = agora.date()
    config = Configuracao.obter()
    saudacao = "Bom dia" if agora.hour < 12 else "Boa tarde" if agora.hour < 18 else "Boa noite"
    unidades = Unidade.objects.filter(ativa=True).select_related("whatsapp", "conta_google")
    contexto = {
        "saudacao": saudacao,
        "hoje": hoje,
        "unidades": unidades,
        "config": config,
        "conversas_robo": Conversa.objects.filter(status=Conversa.Status.ROBO).count(),
        "conversas_humano": Conversa.objects.filter(status=Conversa.Status.HUMANO).count(),
        "agendamentos_hoje": Agendamento.objects.filter(inicio__date=hoje)
        .exclude(status=Agendamento.Status.CANCELADO)
        .count(),
        "erros_24h": RegistroLog.objects.filter(
            nivel__in=["ERROR", "CRITICAL"], criado_em__gte=timezone.now() - timedelta(hours=24)
        ).count(),
        "ultimos_erros": RegistroLog.objects.filter(nivel__in=["ERROR", "CRITICAL"])[:5],
    }
    return render(request, "painel/dashboard.html", contexto)


# --- Integrações ----------------------------------------------------------


@superusuario
def integracoes(request):
    config = Configuracao.obter()
    formularios = {
        "evolution": FormEvolution,
        "ia": FormIA,
        "google": FormGoogle,
    }
    forms = {nome: classe(instance=config, prefix=nome) for nome, classe in formularios.items()}

    if request.method == "POST":
        qual = request.POST.get("formulario")
        if qual in formularios:
            form = formularios[qual](request.POST, instance=config, prefix=qual)
            if form.is_valid():
                for campo, valor in form.cleaned_data.items():
                    setattr(config, campo, valor)
                config.save()
                logger.info("Configuração '%s' atualizada por %s.", qual, request.user)
                if qual == "ia":
                    verificar_ia(config)
                messages.success(request, "Configuração salva.")
                return redirect("painel:integracoes")
            forms[qual] = form

    unidades = Unidade.objects.filter(ativa=True).select_related("whatsapp", "conta_google")
    contexto = {
        "config": config,
        "forms": forms,
        "unidades": unidades,
        "form_numero": FormNumeroWhatsApp(),
        "url_webhook": url_webhook(),
        "url_retorno_google": google.url_retorno(),
        "google_configurado": bool(config.google_client_id and config.google_client_secret),
    }
    return render(request, "painel/integracoes.html", contexto)


@superusuario
@require_POST
def testar_evolution(request):
    try:
        instancias = EvolutionClient().listar_instancias()
        messages.success(request, f"Evolution respondeu. {len(instancias)} instância(s) encontrada(s).")
    except ErroEvolution as e:
        logger.error("Teste da Evolution falhou: %s", e)
        messages.error(request, str(e))
    return redirect("painel:integracoes")


@superusuario
@require_POST
def testar_ia(request):
    status = verificar_ia()
    config = Configuracao.obter()
    nivel = messages.success if status == StatusConexao.CONECTADO else messages.error
    nivel(request, f"IA: {config.get_ia_status_display()}. {config.ia_detalhe_status}")
    return redirect("painel:integracoes")


@superusuario
@require_POST
def whatsapp_conectar(request, unidade_id):
    """Cria a instância na Evolution (se preciso), configura o webhook e abre o QR code."""
    unidade = get_object_or_404(Unidade, pk=unidade_id)
    form = FormNumeroWhatsApp(request.POST)
    digitado = form.cleaned_data["numero"].strip() if form.is_valid() else ""
    numero = normalizar_telefone(digitado)
    if digitado and not numero:
        messages.error(request, "Número inválido. Use DDD + número, por exemplo (31) 99999-0000.")
        return redirect(f"{reverse('painel:integracoes')}#whatsapp")

    instancia, _ = InstanciaWhatsApp.objects.get_or_create(
        unidade=unidade, defaults={"nome_instancia": f"clinica-{slugify(unidade.nome)}-{unidade.pk}"}
    )
    if numero and numero != instancia.numero:
        if instancia.numero:
            logger.info("Número do WhatsApp da unidade %s trocado de +%s para +%s por %s.",
                        unidade, instancia.numero, numero, request.user)
        instancia.numero = numero
        instancia.save(update_fields=["numero"])

    try:
        client = EvolutionClient()
        if not client.instancia_existe(instancia.nome_instancia):
            client.criar_instancia(instancia.nome_instancia, instancia.numero)
            logger.info("Instância %s criada na Evolution.", instancia.nome_instancia)
        client.configurar_webhook(instancia.nome_instancia, url_webhook())
    except ErroEvolution as e:
        logger.error("Falha ao preparar WhatsApp da unidade %s: %s", unidade, e)
        messages.error(request, str(e))
        return redirect("painel:integracoes")

    return redirect("painel:whatsapp_qrcode", unidade_id=unidade.pk)


@superusuario
def whatsapp_qrcode(request, unidade_id):
    instancia = get_object_or_404(InstanciaWhatsApp, unidade_id=unidade_id)
    qrcode, codigo_pareamento, erro = "", "", ""
    if atualizar_status(instancia) != StatusConexao.CONECTADO:
        try:
            client = EvolutionClient()
            # Logo após criar a instância a Evolution ainda está gerando o QR e responde sem imagem.
            for tentativa in range(TENTATIVAS_QRCODE):
                dados = client.obter_qrcode(instancia.nome_instancia, instancia.numero)
                qrcode = dados.get("base64", "")
                codigo_pareamento = dados.get("pairingCode") or ""
                if qrcode or tentativa == TENTATIVAS_QRCODE - 1:
                    break
                time.sleep(1)
        except ErroEvolution as e:
            erro = str(e)
            logger.error("Falha ao obter QR code (%s): %s", instancia.nome_instancia, e)
    return render(
        request,
        "painel/whatsapp_qrcode.html",
        {"instancia": instancia, "qrcode": qrcode, "codigo_pareamento": codigo_pareamento, "erro": erro},
    )


@superusuario
def whatsapp_status(request, unidade_id):
    instancia = get_object_or_404(InstanciaWhatsApp, unidade_id=unidade_id)
    status = atualizar_status(instancia)
    return JsonResponse({"status": status, "rotulo": instancia.get_status_display(), "detalhe": instancia.detalhe_status})


@superusuario
@require_POST
def whatsapp_desconectar(request, unidade_id):
    instancia = get_object_or_404(InstanciaWhatsApp, unidade_id=unidade_id)
    try:
        EvolutionClient().desconectar(instancia.nome_instancia)
    except ErroEvolution as e:
        # Se o celular já saiu (ex.: desconectado pelo próprio WhatsApp), a Evolution recusa o logout.
        # O que importa é o estado final: só é erro se continuar conectado.
        if atualizar_status(instancia) == StatusConexao.CONECTADO:
            logger.error("Falha ao desconectar %s: %s", instancia.nome_instancia, e)
            messages.error(request, str(e))
            return redirect(f"{reverse('painel:integracoes')}#whatsapp")
    atualizar_status(instancia)
    logger.info("WhatsApp da unidade %s desconectado por %s.", instancia.unidade, request.user)
    messages.success(request, f"WhatsApp de {instancia.unidade} desconectado. Para usar outro número, "
                              "informe o novo número e clique em Conectar.")
    return redirect(f"{reverse('painel:integracoes')}#whatsapp")


@superusuario
@require_POST
def whatsapp_robo(request, unidade_id):
    instancia = get_object_or_404(InstanciaWhatsApp, unidade_id=unidade_id)
    instancia.robo_ativo = not instancia.robo_ativo
    instancia.save(update_fields=["robo_ativo"])
    estado = "ligado" if instancia.robo_ativo else "desligado"
    logger.info("Robô da unidade %s %s por %s.", instancia.unidade, estado, request.user)
    messages.success(request, f"Robô {estado} para {instancia.unidade}.")
    return redirect("painel:integracoes")


@superusuario
@require_POST
def verificar_tudo(request):
    for instancia in InstanciaWhatsApp.objects.all():
        atualizar_status(instancia)
    for conta in ContaGoogle.objects.exclude(token_json=""):
        google.atualizar_status(conta)
    verificar_ia()
    messages.info(request, "Status das integrações atualizado.")
    return redirect("painel:integracoes")


# --- Google Agenda --------------------------------------------------------

SALT_OAUTH = "painel.google.oauth"


@superusuario
@require_POST
def google_conectar(request, unidade_id):
    """Inicia o OAuth: manda o navegador para o Google com um `state` assinado e amarrado à sessão."""
    unidade = get_object_or_404(Unidade, pk=unidade_id)
    nonce = secrets.token_urlsafe(16)
    request.session["google_oauth_nonce"] = nonce
    state = signing.dumps({"u": unidade.pk, "n": nonce}, salt=SALT_OAUTH)
    try:
        return redirect(google.url_autorizacao(state))
    except google.ErroGoogle as e:
        messages.error(request, str(e))
        return redirect("painel:integracoes")


@superusuario
def google_callback(request):
    """Retorno do Google após o login/consentimento."""
    nonce_sessao = request.session.pop("google_oauth_nonce", None)
    try:
        dados = signing.loads(request.GET.get("state", ""), salt=SALT_OAUTH, max_age=600)
    except signing.BadSignature:
        logger.warning("Retorno do Google com state inválido ou expirado (usuário %s).", request.user)
        messages.error(request, "A conexão com o Google expirou ou é inválida. Tente de novo.")
        return redirect("painel:integracoes")
    if not nonce_sessao or not secrets.compare_digest(dados.get("n", ""), nonce_sessao):
        logger.warning("Retorno do Google com nonce divergente da sessão (usuário %s).", request.user)
        messages.error(request, "A conexão com o Google não foi iniciada nesta sessão. Tente de novo.")
        return redirect("painel:integracoes")

    unidade = get_object_or_404(Unidade, pk=dados["u"])

    if request.GET.get("error"):
        # Ex.: access_denied quando a pessoa cancela na tela do Google.
        logger.info("Conexão com o Google cancelada para %s: %s", unidade, request.GET["error"])
        messages.warning(request, "A conexão com o Google foi cancelada.")
        return redirect("painel:integracoes")

    try:
        token, email = google.trocar_codigo(request.GET.get("code", ""))
    except google.ErroGoogle as e:
        logger.error("Falha ao conectar Google da unidade %s: %s", unidade, e)
        messages.error(request, str(e))
        return redirect("painel:integracoes")

    conta, _ = ContaGoogle.objects.get_or_create(unidade=unidade)
    conta.token_json = json.dumps(token)
    conta.email = email
    conta.status = StatusConexao.CONECTADO
    conta.detalhe_status = "Conectado agora."
    conta.ultima_verificacao = timezone.now()
    conta.save()
    logger.info("Google Agenda da unidade %s conectado (%s) por %s.", unidade, email or "e-mail desconhecido", request.user)
    messages.success(request, f"Google Agenda conectado para {unidade}. Escolha o calendário de cada dentista.")
    return redirect("painel:google_unidade", unidade_id=unidade.pk)


@superusuario
@require_POST
def google_desconectar(request, unidade_id):
    conta = get_object_or_404(ContaGoogle, unidade_id=unidade_id)
    if conta.token_json:
        google.revogar(json.loads(conta.token_json))
    conta.token_json = ""
    conta.email = ""
    conta.status = StatusConexao.NAO_CONFIGURADO
    conta.detalhe_status = ""
    conta.save()
    logger.info("Google Agenda da unidade %s desconectado por %s.", conta.unidade, request.user)
    messages.success(request, f"Google Agenda desconectado de {conta.unidade}.")
    return redirect("painel:integracoes")


def _data_ou_hoje(valor):
    try:
        return date.fromisoformat(valor)
    except (TypeError, ValueError):
        return timezone.localdate()


@superusuario
def google_unidade(request, unidade_id):
    """Calendário de cada dentista + teste de horários livres."""
    unidade = get_object_or_404(Unidade, pk=unidade_id)
    conta = getattr(unidade, "conta_google", None)
    if conta is None or not conta.token_json:
        messages.info(request, f"Conecte o Google Agenda de {unidade} primeiro.")
        return redirect("painel:integracoes")

    dentistas = list(
        unidade.dentistas.filter(ativo=True).annotate(qtd_horarios=Count("horarios")).prefetch_related("procedimentos")
    )
    calendarios, erro_calendarios = [], ""
    try:
        calendarios = google.GoogleAgenda(conta).listar_calendarios()
    except google.ErroGoogle as e:
        erro_calendarios = str(e)
        logger.error("Falha ao listar calendários da unidade %s: %s", unidade, e)
        google.atualizar_status(conta)

    if request.method == "POST" and calendarios:
        validos = {c["id"] for c in calendarios}
        principal = next((c["id"] for c in calendarios if c["principal"]), "")
        alterados = 0
        for d in dentistas:
            escolhido = request.POST.get(f"calendario_{d.pk}", "")
            if escolhido not in validos:
                continue
            # O principal fica gravado vazio: o sistema usa "primary" (sobrevive a troca de e-mail da conta).
            novo = "" if escolhido == principal else escolhido
            if novo != d.google_calendar_id:
                d.google_calendar_id = novo
                d.save(update_fields=["google_calendar_id"])
                alterados += 1
        logger.info("Calendários da unidade %s salvos por %s (%d alterado(s)).", unidade, request.user, alterados)
        messages.success(request, "Calendários salvos.")
        return redirect("painel:google_unidade", unidade_id=unidade.pk)

    principal = next((c["id"] for c in calendarios if c["principal"]), "")
    for d in dentistas:
        d.calendario_atual = d.google_calendar_id or principal

    # Modo "remarcar": dentista e procedimento são os da consulta; o horário dela conta como livre.
    remarcando = None
    if request.GET.get("remarcar"):
        remarcando = (
            Agendamento.objects.filter(pk=request.GET["remarcar"], dentista__unidade=unidade)
            .exclude(status=Agendamento.Status.CANCELADO)
            .select_related("paciente", "dentista", "procedimento")
            .first()
        )

    # Teste de disponibilidade (GET, não altera nada)
    teste = None
    if (request.GET.get("dentista") or remarcando) and calendarios:
        if remarcando:
            dentista = next((d for d in dentistas if d.pk == remarcando.dentista_id), None)
            procedimento = remarcando.procedimento
        else:
            dentista = next((d for d in dentistas if str(d.pk) == request.GET["dentista"]), None)
            procedimento = Procedimento.objects.filter(pk=request.GET.get("procedimento"), clinica=unidade.clinica).first()
        data_inicial = _data_ou_hoje(request.GET.get("data"))
        if dentista and procedimento:
            teste = {"dentista": dentista, "procedimento": procedimento, "data": data_inicial}
            if not dentista.qtd_horarios:
                # Sem janela de atendimento não há o que calcular — nem consulta o Google.
                teste["sem_horarios"] = True
            else:
                try:
                    teste["dias"] = buscar_horarios_livres(dentista, procedimento, data_inicial, dias=5, ignorar=remarcando)
                except google.ErroGoogle as e:
                    teste["erro"] = str(e)
                    logger.error("Teste de disponibilidade falhou (%s): %s", unidade, e)

    proximas = (
        Agendamento.objects.filter(dentista__unidade=unidade, fim__gte=timezone.now())
        .select_related("paciente", "dentista", "procedimento")
        .order_by("inicio")[:30]
    )

    contexto = {
        "proximas": proximas,
        "remarcando": remarcando,
        "unidade": unidade,
        "conta": conta,
        "dentistas": dentistas,
        "calendarios": calendarios,
        "erro_calendarios": erro_calendarios,
        "procedimentos": Procedimento.objects.filter(clinica=unidade.clinica, ativo=True).order_by("nome"),
        "teste": teste,
        "hoje": timezone.localdate(),
        "filtro": {
            "dentista": request.GET.get("dentista", ""),
            "procedimento": request.GET.get("procedimento", ""),
            "data": request.GET.get("data", "") or timezone.localdate().isoformat(),
        },
    }
    return render(request, "painel/google_unidade.html", contexto)


# --- Consultas (marcar / confirmar / remarcar / cancelar) ----------------


def _inicio_do_post(request):
    try:
        inicio = datetime.fromisoformat(request.POST.get("inicio", ""))
    except ValueError:
        return None
    return inicio if timezone.is_aware(inicio) else timezone.make_aware(inicio)


def _voltar_agenda(unidade_id):
    return redirect(f"{reverse('painel:google_unidade', args=[unidade_id])}#consultas")


def _voltar_mesma_pagina(request, unidade_id):
    """Volta para a página de onde veio (mantém o filtro do teste), só se for deste site."""
    origem = request.META.get("HTTP_REFERER", "")
    if url_has_allowed_host_and_scheme(origem, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(origem)
    return _voltar_agenda(unidade_id)


@superusuario
@require_POST
def consulta_marcar(request, unidade_id):
    unidade = get_object_or_404(Unidade, pk=unidade_id)
    dentista = get_object_or_404(Dentista, pk=request.POST.get("dentista"), unidade=unidade, ativo=True)
    procedimento = get_object_or_404(Procedimento, pk=request.POST.get("procedimento"), clinica=unidade.clinica)
    inicio = _inicio_do_post(request)
    telefone = normalizar_telefone(request.POST.get("telefone"))
    nome = request.POST.get("nome", "").strip()[:150]
    if not inicio or not telefone or not nome:
        messages.error(request, "Informe o nome e um telefone válido do paciente (com DDD) e escolha um horário.")
        return _voltar_mesma_pagina(request, unidade.pk)

    paciente, criado = Paciente.objects.get_or_create(clinica=unidade.clinica, telefone=telefone, defaults={"nome": nome})
    if not criado and not paciente.nome:
        paciente.nome = nome
        paciente.save(update_fields=["nome"])
    try:
        ag = agendamentos.marcar(paciente, dentista, procedimento, inicio, origem=Agendamento.Origem.PAINEL)
    except agendamentos.ErroAgendamento as e:
        messages.error(request, str(e))
        return _voltar_mesma_pagina(request, unidade.pk)
    messages.success(request, f"Consulta marcada: {paciente} em {timezone.localtime(ag.inicio):%d/%m às %H:%M}. Confira no Google Agenda.")
    return _voltar_agenda(unidade.pk)


def _agendamento_da_acao(request, agendamento_id):
    return get_object_or_404(
        Agendamento.objects.select_related("paciente", "dentista__unidade", "procedimento"), pk=agendamento_id
    )


@superusuario
@require_POST
def consulta_confirmar(request, agendamento_id):
    ag = _agendamento_da_acao(request, agendamento_id)
    try:
        agendamentos.confirmar(ag)
        messages.success(request, f"Consulta de {ag.paciente} confirmada.")
    except agendamentos.ErroAgendamento as e:
        messages.error(request, str(e))
    return _voltar_agenda(ag.dentista.unidade_id)


@superusuario
@require_POST
def consulta_cancelar(request, agendamento_id):
    ag = _agendamento_da_acao(request, agendamento_id)
    try:
        agendamentos.cancelar(ag, motivo=f"Cancelada pelo painel ({request.user})")
        messages.success(request, f"Consulta de {ag.paciente} cancelada. O horário voltou a ficar livre.")
    except agendamentos.ErroAgendamento as e:
        messages.error(request, str(e))
    return _voltar_agenda(ag.dentista.unidade_id)


@superusuario
@require_POST
def consulta_remarcar(request, agendamento_id):
    ag = _agendamento_da_acao(request, agendamento_id)
    inicio = _inicio_do_post(request)
    if not inicio:
        messages.error(request, "Escolha o novo horário.")
        return _voltar_agenda(ag.dentista.unidade_id)
    try:
        agendamentos.remarcar(ag, inicio)
        messages.success(request, f"Consulta de {ag.paciente} remarcada para {timezone.localtime(inicio):%d/%m às %H:%M}. Aguardando nova confirmação.")
    except agendamentos.ErroAgendamento as e:
        messages.error(request, str(e))
    return _voltar_agenda(ag.dentista.unidade_id)


# --- Caixa de entrada (conversas) -----------------------------------------

# chave: (rótulo curto da aba, descrição completa, filtro)
FILTROS_CONVERSA = {
    "equipe": ("Equipe", "Conversas com a equipe", {"status": Conversa.Status.HUMANO}),
    "assistente": ("Assistente", "Conversas com o assistente", {"status": Conversa.Status.ROBO}),
    "encerradas": ("Encerradas", "Conversas encerradas", {"status": Conversa.Status.ENCERRADA}),
    "todas": ("Todas", "Todas as conversas", {}),
}


def _conversas_base():
    return Conversa.objects.filter(simulada=False).select_related("paciente", "unidade", "atendente")


def _mensagem_json(m):
    return {
        "id": m.pk,
        "origem": m.origem,
        "texto": m.texto,
        "hora": timezone.localtime(m.criada_em).strftime("%d/%m %H:%M"),
        "autor": (m.autor.get_short_name() or m.autor.get_username()) if m.autor_id else "",
        "falhou": m.id_externo.startswith("falha:"),
    }


def _conversa_json(conversa, depois=0):
    mensagens = conversa.mensagens.select_related("autor").order_by("criada_em", "pk")
    if depois:
        mensagens = mensagens.filter(pk__gt=depois)
    return {
        "status": conversa.status,
        "status_rotulo": conversa.get_status_display(),
        "atendente": str(conversa.atendente) if conversa.atendente_id else "",
        "mensagens": [_mensagem_json(m) for m in mensagens],
    }


@superusuario
def conversas(request):
    base = _conversas_base()
    unidades = list(Unidade.objects.filter(ativa=True).order_by("nome"))
    unidade_id = request.GET.get("unidade", "")
    if unidade_id:
        base = base.filter(unidade_id=unidade_id)
    contagens = {chave: base.filter(**filtro).count() for chave, (_, _, filtro) in FILTROS_CONVERSA.items()}
    filtro = request.GET.get("filtro") or ("equipe" if contagens["equipe"] else "todas")
    if filtro not in FILTROS_CONVERSA:
        filtro = "todas"

    lista = base.filter(**FILTROS_CONVERSA[filtro][2])
    busca = request.GET.get("q", "").strip()
    if busca:
        digitos = "".join(c for c in busca if c.isdigit())
        condicao = Q(paciente__nome__icontains=busca)
        if digitos:
            condicao |= Q(paciente__telefone__contains=digitos)
        lista = lista.filter(condicao)
    lista = list(lista.order_by(F("ultima_mensagem_em").desc(nulls_last=True), "-pk")[:100])

    selecionada = None
    if request.GET.get("c"):
        selecionada = _conversas_base().filter(pk=request.GET["c"]).first()
    proximas = []
    if selecionada:
        proximas = (
            Agendamento.objects.filter(paciente=selecionada.paciente, fim__gte=timezone.now())
            .select_related("procedimento", "dentista").order_by("inicio")[:5]
        )

    parametros = request.GET.copy()
    parametros.pop("c", None)
    return render(request, "painel/conversas.html", {
        "lista": lista,
        "filtros": [
            (chave, rotulo, descricao, contagens[chave]) for chave, (rotulo, descricao, _) in FILTROS_CONVERSA.items()
        ],
        "filtro": filtro,
        "busca": busca,
        "unidades": unidades,
        "unidade_id": unidade_id,
        "selecionada": selecionada,
        "dados_conversa": _conversa_json(selecionada) if selecionada else None,
        "proximas": proximas,
        "querystring": parametros.urlencode(),
        "assinatura_lista": _assinatura_lista(),
        "hoje": timezone.localdate(),
    })


def _assinatura_lista():
    """Muda quando chega/sai mensagem ou muda status em qualquer conversa (para o painel saber se recarrega)."""
    ultima = _conversas_base().order_by("-atualizada_em").values_list("atualizada_em", flat=True).first()
    return ultima.isoformat() if ultima else ""


@superusuario
def conversa_atualizacoes(request, conversa_id):
    """Polling da tela: mensagens novas da conversa aberta + se a lista mudou."""
    conversa = get_object_or_404(_conversas_base(), pk=conversa_id)
    try:
        depois = int(request.GET.get("depois", 0))
    except ValueError:
        depois = 0
    return JsonResponse({**_conversa_json(conversa, depois), "assinatura_lista": _assinatura_lista()})


@superusuario
def conversas_assinatura(request):
    return JsonResponse({"assinatura_lista": _assinatura_lista()})


@superusuario
@require_POST
def conversa_responder(request, conversa_id):
    from integracoes.evolution import ErroEvolution

    conversa = get_object_or_404(_conversas_base(), pk=conversa_id)
    texto = request.POST.get("texto", "").strip()[:4000]
    if not texto:
        return JsonResponse({"erro": "Escreva a mensagem."}, status=400)
    if conversa.status == Conversa.Status.ENCERRADA:
        return JsonResponse({"erro": "Conversa encerrada. O paciente precisa mandar uma mensagem nova."}, status=409)
    try:
        enviar_mensagem_equipe(conversa, texto, request.user)
        erro = None
    except ErroEvolution as e:
        erro = f"A mensagem não foi enviada pelo WhatsApp: {e}"
    conversa.refresh_from_db()
    resposta = _conversa_json(conversa)
    if erro:
        resposta["erro"] = erro
    return JsonResponse(resposta, status=502 if erro else 200)


@superusuario
@require_POST
def conversa_nota(request, conversa_id):
    conversa = get_object_or_404(_conversas_base(), pk=conversa_id)
    texto = request.POST.get("texto", "").strip()[:4000]
    if not texto:
        return JsonResponse({"erro": "Escreva a nota."}, status=400)
    registrar_nota(conversa, texto, request.user)
    return JsonResponse(_conversa_json(conversa))


def _voltar_conversa(request, conversa):
    origem = request.POST.get("voltar", "")
    if origem.startswith("/conversas/") and url_has_allowed_host_and_scheme(origem, allowed_hosts={request.get_host()}):
        return redirect(origem)
    return redirect(f"{reverse('painel:conversas')}?c={conversa.pk}")


@superusuario
@require_POST
def conversa_assumir(request, conversa_id):
    conversa = get_object_or_404(_conversas_base(), pk=conversa_id)
    assumir_conversa(conversa, request.user, motivo=f"Assumida por {request.user}")
    logger.info("Conversa %s (%s) assumida por %s.", conversa.pk, conversa.paciente, request.user)
    messages.success(request, "Você assumiu a conversa. O assistente não responde até você devolver.")
    return _voltar_conversa(request, conversa)


@superusuario
@require_POST
def conversa_devolver(request, conversa_id):
    conversa = get_object_or_404(_conversas_base(), pk=conversa_id)
    devolver_ao_assistente(conversa, request.user)
    messages.success(request, "Conversa devolvida ao assistente. Ele responde a próxima mensagem do paciente.")
    return _voltar_conversa(request, conversa)


@superusuario
@require_POST
def conversa_encerrar(request, conversa_id):
    conversa = get_object_or_404(_conversas_base(), pk=conversa_id)
    encerrar_conversa(conversa, request.user)
    messages.success(request, "Conversa encerrada. Se o paciente escrever de novo, abre uma conversa nova com o assistente.")
    return _voltar_conversa(request, conversa)


# --- Assistente (configuração + simulador) -------------------------------


def _paciente_simulador(request, clinica):
    """Paciente fictício do simulador (um por usuário do painel)."""
    paciente, _ = Paciente.objects.get_or_create(
        clinica=clinica, telefone=f"sim{request.user.pk}", defaults={"nome": ""}
    )
    return paciente


def _conversa_simulada(request, unidade, criar=True):
    paciente = _paciente_simulador(request, unidade.clinica)
    conversa = (
        Conversa.objects.filter(paciente=paciente, unidade=unidade, simulada=True)
        .exclude(status=Conversa.Status.ENCERRADA).first()
    )
    if conversa is None and criar:
        conversa = Conversa.objects.create(paciente=paciente, unidade=unidade, simulada=True)
    return conversa


def _mensagens_json(conversa):
    if conversa is None:
        return []
    return [
        {"origem": m.origem, "texto": m.texto, "hora": timezone.localtime(m.criada_em).strftime("%H:%M")}
        for m in conversa.mensagens.order_by("criada_em")
    ]


@superusuario
def assistente(request):
    config = Configuracao.obter()
    qual = request.POST.get("formulario", "assistente") if request.method == "POST" else ""
    form = FormAssistente(request.POST if qual == "assistente" else None, instance=config, prefix="assistente")
    form_lembretes = FormLembretes(request.POST if qual == "lembretes" else None, instance=config, prefix="lembretes")
    alvo = {"assistente": form, "lembretes": form_lembretes}.get(qual)
    if alvo is not None and alvo.is_valid():
        alvo.save()
        logger.info("Configuração '%s' atualizada por %s.", qual, request.user)
        messages.success(request, "Configuração salva.")
        return redirect(f"{reverse('painel:assistente')}{'#lembretes' if qual == 'lembretes' else ''}")
    unidades = list(Unidade.objects.filter(ativa=True).order_by("nome"))
    unidade = next((u for u in unidades if str(u.pk) == request.GET.get("unidade")), unidades[0] if unidades else None)
    conversa = _conversa_simulada(request, unidade, criar=False) if unidade else None
    return render(request, "painel/assistente.html", {
        "form": form,
        "form_lembretes": form_lembretes,
        "fila_configurada": bool(settings.CELERY_BROKER_URL),
        "fila_com_worker": bool(settings.CELERY_BROKER_URL) and celery_tem_worker(),
        "config": config,
        "unidades": unidades,
        "unidade": unidade,
        "conversa": conversa,
        "mensagens": _mensagens_json(conversa),
        "ia_pronta": bool(config.ia_api_key),
    })


@superusuario
@require_POST
def assistente_lembretes_agora(request):
    """Botão de teste: envia já o que estiver pendente (ignora a janela 8h–20h)."""
    from atendimento.lembretes import executar

    r = executar(ignorar_janela=True)
    logger.info("Envio manual de lembretes por %s: %d confirmação(ões), %d lembrete(s), %d falha(s).",
                request.user, r.confirmacoes, r.lembretes, r.falhas)
    if r.confirmacoes or r.lembretes:
        messages.success(request, f"Enviados: {r.confirmacoes} pedido(s) de confirmação e {r.lembretes} lembrete(s).")
    if r.falhas:
        messages.error(request, f"{r.falhas} envio(s) falharam — veja os Logs.")
    if r.motivo:
        messages.info(request, r.motivo)
    return redirect(f"{reverse('painel:assistente')}#lembretes")


@superusuario
@require_POST
def assistente_simular(request):
    """Mensagem do 'paciente' no simulador → resposta do assistente (síncrono)."""
    from atendimento.assistente import ErroAssistente, descrever_erro_api, responder

    unidade = get_object_or_404(Unidade, pk=request.POST.get("unidade"), ativa=True)
    texto = request.POST.get("texto", "").strip()[:2000]
    if not texto:
        return JsonResponse({"erro": "Escreva uma mensagem."}, status=400)
    conversa = _conversa_simulada(request, unidade)
    if conversa.status != Conversa.Status.ROBO:
        return JsonResponse({"erro": "Esta conversa foi transferida para a equipe. Reinicie a simulação.",
                             "mensagens": _mensagens_json(conversa), "transferida": True}, status=409)
    Mensagem.objects.create(conversa=conversa, origem=Mensagem.Origem.PACIENTE, texto=texto)
    try:
        resultado = responder(conversa)
    except ErroAssistente as e:
        logger.error("Simulador: %s", e)
        return JsonResponse({"erro": str(e), "mensagens": _mensagens_json(conversa)}, status=502)
    except Exception as e:
        motivo = descrever_erro_api(e)
        logger.exception("Simulador: falha na IA: %s", motivo)
        return JsonResponse({"erro": motivo, "mensagens": _mensagens_json(conversa)}, status=502)

    texto_resposta = resultado.texto or (TEXTO_TRANSFERENCIA if resultado.transferiu else "")
    if texto_resposta:
        enviar_resposta(conversa, texto_resposta)
    conversa.refresh_from_db()
    return JsonResponse({
        "mensagens": _mensagens_json(conversa),
        "acoes": resultado.acoes,
        "transferida": conversa.status == Conversa.Status.HUMANO,
    })


@superusuario
@require_POST
def assistente_reiniciar(request):
    unidade = get_object_or_404(Unidade, pk=request.POST.get("unidade"))
    conversa = _conversa_simulada(request, unidade, criar=False)
    if conversa:
        conversa.status = Conversa.Status.ENCERRADA
        conversa.save(update_fields=["status"])
    paciente = _paciente_simulador(request, unidade.clinica)
    paciente.nome = ""
    paciente.save(update_fields=["nome"])
    return redirect(f"{reverse('painel:assistente')}?unidade={unidade.pk}#simulador")


# --- Logs -----------------------------------------------------------------


@superusuario
def logs(request):
    qs = RegistroLog.objects.all()
    nivel = request.GET.get("nivel", "")
    origem = request.GET.get("origem", "")
    busca = request.GET.get("q", "")
    if nivel == "erros":
        qs = qs.filter(nivel__in=["ERROR", "CRITICAL"])
    elif nivel:
        qs = qs.filter(nivel=nivel)
    if origem:
        qs = qs.filter(origem=origem)
    if busca:
        qs = qs.filter(mensagem__icontains=busca)

    pagina = Paginator(qs, 50).get_page(request.GET.get("pagina"))
    origens = RegistroLog.objects.order_by("origem").values_list("origem", flat=True).distinct()
    filtros = request.GET.copy()
    filtros.pop("pagina", None)
    return render(
        request,
        "painel/logs.html",
        {
            "pagina": pagina,
            "origens": origens,
            "niveis": [n for n, _ in RegistroLog.NIVEIS],
            "filtro": {"nivel": nivel, "origem": origem, "q": busca},
            "querystring": filtros.urlencode(),
        },
    )
