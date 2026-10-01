import logging
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from atendimento.models import Agendamento, Conversa
from core.models import RegistroLog, Unidade
from integracoes.evolution import ErroEvolution, EvolutionClient, atualizar_status, url_webhook
from integracoes.ia import verificar_ia
from integracoes.models import Configuracao, ContaGoogle, InstanciaWhatsApp, StatusConexao

from .forms import FormEvolution, FormGoogle, FormIA, FormNumeroWhatsApp

logger = logging.getLogger("clinica.painel")


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
    hoje = timezone.localdate()
    config = Configuracao.obter()
    unidades = Unidade.objects.filter(ativa=True).select_related("whatsapp", "conta_google")
    contexto = {
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
    numero = form.cleaned_data["numero"].strip() if form.is_valid() else ""

    instancia, _ = InstanciaWhatsApp.objects.get_or_create(
        unidade=unidade, defaults={"nome_instancia": f"clinica-{slugify(unidade.nome)}-{unidade.pk}"}
    )
    if numero:
        instancia.numero = "".join(c for c in numero if c.isdigit())
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
            dados = EvolutionClient().obter_qrcode(instancia.nome_instancia)
            qrcode = dados.get("base64", "")
            codigo_pareamento = dados.get("pairingCode") or ""
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
        logger.info("WhatsApp da unidade %s desconectado por %s.", instancia.unidade, request.user)
        messages.success(request, "WhatsApp desconectado.")
    except ErroEvolution as e:
        logger.error("Falha ao desconectar %s: %s", instancia.nome_instancia, e)
        messages.error(request, str(e))
    atualizar_status(instancia)
    return redirect("painel:integracoes")


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
    verificar_ia()
    messages.info(request, "Status das integrações atualizado.")
    return redirect("painel:integracoes")


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
