from datetime import timedelta
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from integracoes.evolution import atualizar_status, url_webhook
from integracoes.models import Configuracao, InstanciaWhatsApp, StatusConexao
from core.models import Unidade
from painel.forms import FormEvolution
from painel.views import superusuario
from .forms import FormConfiguracao, FormContato, FormImportar, FormTeste
from .importacao import importar
from .models import ConfiguracaoDisparo, Contato, Tentativa
from .services import elegiveis, executar_tentativa, reservar


@superusuario
def inicio(request):
    config = ConfiguracaoDisparo.obter()
    form = FormConfiguracao(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            atual = ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
            if atual.ativo:
                messages.error(request, 'Pause a fila antes de editar a configuração.')
            else:
                for campo, valor in form.cleaned_data.items():
                    setattr(atual, campo, valor)
                atual.save(update_fields=list(form.cleaned_data))
                messages.success(request, 'Configuração salva. A fila permanece pausada.')
        return redirect('painel:dashboard')
    return render(request, 'disparos/inicio.html', {'config': config, 'form': form,
        'form_teste': FormTeste(), 'total': Contato.objects.filter(excluido_em__isnull=True).count(), 'pendentes': elegiveis(config).count(),
        'enviados': Tentativa.objects.filter(status='enviado').count(), 'revisar': Contato.objects.filter(excluido_em__isnull=True).exclude(revisao='').count(),
        'ultimos': Tentativa.objects.select_related('contato')[:8],
        'instancia': InstanciaWhatsApp.objects.filter(unidade_id=config.unidade_id).first(),
        'agendador_recente': bool(config.ultima_execucao and config.ultima_execucao > timezone.now() - timedelta(minutes=3))})


@superusuario
@require_POST
def alternar(request):
    ConfiguracaoDisparo.obter()
    with transaction.atomic():
        config = ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
        if request.POST.get('acao') == 'pausar':
            config.ativo = False
        elif request.POST.get('acao') == 'iniciar':
            form = FormConfiguracao(instance=config, data={'unidade': config.unidade_id, 'mensagem': config.mensagem, 'horarios': config.horarios, 'tamanho_lote': config.tamanho_lote, 'intervalo_segundos': config.intervalo_segundos})
            conectado = InstanciaWhatsApp.objects.filter(unidade_id=config.unidade_id, status=StatusConexao.CONECTADO).exists()
            if not form.is_valid() or not conectado or not elegiveis(config).exists():
                messages.error(request, 'Confira a mensagem, conecte o WhatsApp e importe pacientes elegíveis antes de iniciar.')
                return redirect('painel:dashboard')
            config.ativo = True
        config.save(update_fields=['ativo'])
    messages.success(request, 'Fila ativa nos horários configurados.' if config.ativo else 'Fila pausada. Uma chamada já enviada à Evolution não pode ser desfeita.')
    return redirect('painel:dashboard')


@superusuario
def pacientes(request):
    form = FormImportar(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        try:
            novos, atualizados, revisao = importar(form.cleaned_data['arquivo'], form.cleaned_data['unidade'])
        except (ValueError, UnicodeError) as e:
            form.add_error('arquivo', str(e))
        else:
            messages.success(request, f'{novos} novos; {atualizados} atualizados; {revisao} precisam de revisão. Reservas anteriores preservadas.')
            return redirect('painel:pacientes')
    contatos = Contato.objects.filter(excluido_em__isnull=True).select_related('unidade')
    busca, filtro, grupo = request.GET.get('q', '').strip(), request.GET.get('estado', ''), request.GET.get('grupo', '')
    if busca:
        contatos = contatos.filter(Q(nome__icontains=busca) | Q(telefone_original__icontains=busca) | Q(id_origem=busca))
    if grupo:
        contatos = contatos.filter(grupo=grupo)
    if filtro == 'pendentes':
        contatos = contatos.filter(automatico_reservado_em__isnull=True, revisao='', selecionado=True)
    elif filtro == 'reservados':
        contatos = contatos.filter(automatico_reservado_em__isnull=False)
    elif filtro == 'revisao':
        contatos = contatos.exclude(revisao='')
    elif filtro == 'excluidos':
        contatos = contatos.filter(selecionado=False)
    query = request.GET.copy()
    query.pop('pagina', None)
    return render(request, 'disparos/pacientes.html', {'form': form,
        'pagina': Paginator(contatos, 50).get_page(request.GET.get('pagina')), 'busca': busca, 'estado': filtro, 'grupo': grupo,
        'grupos': Contato.objects.filter(excluido_em__isnull=True).values_list('grupo', flat=True).distinct().order_by('grupo'), 'querystring': query.urlencode()})


@superusuario
def contato(request, contato_id):
    paciente = get_object_or_404(Contato, pk=contato_id, excluido_em__isnull=True)
    form = FormContato(request.POST or None, instance=paciente)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
            editado = form.save(commit=False)
            # Não gravar o snapshot antigo de automatico_reservado_em carregado antes do bloqueio.
            Contato.objects.filter(pk=paciente.pk, excluido_em__isnull=True).update(nome=editado.nome, telefone=editado.telefone,
                chave_numero=editado.chave_numero, selecionado=editado.selecionado, revisao=editado.revisao)
        messages.success(request, 'Cadastro atualizado. Isso não libera uma nova tentativa automática.')
        return redirect('painel:contato', contato_id=paciente.pk)
    config, previa = ConfiguracaoDisparo.obter(), ''
    if paciente.nome.strip():
        try:
            previa = config.mensagem.format(nome=paciente.nome, primeiro_nome=paciente.nome.split()[0])
        except (ValueError, KeyError):
            pass
    return render(request, 'disparos/contato.html', {'paciente': paciente, 'form': form,
        'tentativas': paciente.tentativas.all(), 'previa': previa, 'ultima': paciente.tentativas.first()})


@superusuario
@require_POST
def excluir_contato(request, contato_id):
    ConfiguracaoDisparo.obter()
    with transaction.atomic():
        ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
        paciente = get_object_or_404(Contato.objects.select_for_update(), pk=contato_id)
        if not paciente.excluido_em:
            paciente.excluido_em = timezone.now()
            paciente.selecionado = False
            paciente.save(update_fields=['excluido_em', 'selecionado'])
    messages.success(request, 'Paciente excluído da lista e de novos envios. Histórico e bloqueios preservados. Um envio já iniciado não pode ser desfeito.')
    return redirect('painel:pacientes')


@superusuario
@require_POST
def tentar_manualmente(request, contato_id):
    paciente = get_object_or_404(Contato, pk=contato_id)
    tentativa = reservar(paciente.pk, manual=True, autor=request.user)
    if tentativa:
        executar_tentativa(tentativa.pk)
        messages.info(request, 'Tentativa manual registrada. Confira o resultado no histórico.')
    else:
        messages.error(request, 'Tentativa não iniciada. Confira cadastro, unidade, conexão, intervalo e tentativas em andamento. Envios aceitos não podem ser repetidos.')
    return redirect('painel:contato', contato_id=paciente.pk)


@superusuario
def historico(request):
    tentativas = Tentativa.objects.select_related('contato', 'autor')
    status, busca = request.GET.get('status', ''), request.GET.get('q', '').strip()
    if status:
        tentativas = tentativas.filter(status=status)
    if busca:
        tentativas = tentativas.filter(Q(nome__icontains=busca) | Q(telefone_usado__icontains=busca))
    query = request.GET.copy()
    query.pop('pagina', None)
    return render(request, 'disparos/historico.html', {'pagina': Paginator(tentativas, 50).get_page(request.GET.get('pagina')),
        'estados': Tentativa.Status.choices, 'estado': status, 'busca': busca, 'querystring': query.urlencode()})


@superusuario
def integracoes(request):
    config = Configuracao.obter()
    form = FormEvolution(request.POST or None, instance=config, prefix='evolution')
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Conexão Evolution salva.')
        return redirect('painel:integracoes')
    return render(request, 'disparos/integracoes.html', {'form': form,
        'unidades': Unidade.objects.filter(ativa=True).select_related('whatsapp'), 'url_webhook': url_webhook()})


@superusuario
@require_POST
def verificar_tudo(request):
    for instancia in InstanciaWhatsApp.objects.select_related('unidade'):
        atualizar_status(instancia)
    messages.info(request, 'Status dos WhatsApps atualizado.')
    return redirect('painel:integracoes')


@superusuario
@require_POST
def enviar_teste(request):
    from .telefones import chave
    form = FormTeste(request.POST)
    config = ConfiguracaoDisparo.obter()
    if not form.is_valid() or not config.unidade_id or not config.mensagem:
        messages.error(request, 'Salve a mensagem e a unidade e informe nome e telefone válidos para o teste.')
        return redirect('painel:dashboard')
    with transaction.atomic():
        ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
        paciente, _ = Contato.objects.get_or_create(unidade=config.unidade,
            id_origem='teste:' + chave(form.cleaned_data['telefone']), defaults={
                'nome': form.cleaned_data['nome'], 'telefone_original': form.cleaned_data['telefone'],
                'telefone': form.cleaned_data['telefone'], 'chave_numero': chave(form.cleaned_data['telefone']),
                'selecionado': False, 'grupo': 'Teste manual'})
    tentativa = reservar(paciente.pk, manual=True, teste=True, autor=request.user)
    if tentativa:
        executar_tentativa(tentativa.pk)
        messages.info(request, 'Teste registrado. Confira o resultado antes de ativar a fila.')
    else:
        messages.error(request, 'Teste não iniciado: confira conexão, intervalo e se este número já recebeu.')
    return redirect('painel:contato', contato_id=paciente.pk)


@superusuario
@require_POST
def selecionar(request):
    ids = request.POST.getlist('contatos')[:50]
    if any(not valor.isdigit() for valor in ids):
        messages.error(request, 'Seleção inválida.')
    else:
        with transaction.atomic():
            ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
            quantidade = Contato.objects.filter(pk__in=ids, excluido_em__isnull=True).update(selecionado=request.POST.get('acao') == 'incluir')
        messages.success(request, f'{quantidade} cadastro(s) atualizados. Bloqueios anteriores permanecem.')
    return redirect('painel:pacientes')
