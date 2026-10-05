"""Disponibilidade de horários: horário de atendimento − agenda ocupada − antecedência mínima.

`calcular_horarios_livres` é pura (sem banco nem Google) para ser testável;
`buscar_horarios_livres` junta os dados reais e é o que a IA vai usar.
"""

from datetime import datetime, timedelta

from django.utils import timezone

from integracoes.google import ErroGoogle, GoogleAgenda

from .models import Agendamento

# Intervalo entre inícios de consulta oferecidos ao paciente.
PASSO_MINUTOS = 30
# Não oferecer horários que comecem antes de agora + isso.
ANTECEDENCIA_MINUTOS = 120
# Limite de dias consultados de uma vez (protege a cota da API).
MAX_DIAS = 31


def calcular_horarios_livres(janelas, duracao_minutos, ocupados, agora,
                             passo_minutos=PASSO_MINUTOS, antecedencia_minutos=ANTECEDENCIA_MINUTOS):
    """Inícios possíveis de consulta.

    janelas: [(inicio, fim)] em que o dentista atende (datetimes aware).
    ocupados: [(inicio, fim)] já tomados na agenda.
    Um horário vale se a consulta inteira cabe na janela e não encosta em nenhum ocupado.
    """
    duracao = timedelta(minutes=duracao_minutos)
    passo = timedelta(minutes=passo_minutos)
    minimo = agora + timedelta(minutes=antecedencia_minutos)
    livres = []
    for inicio_janela, fim_janela in janelas:
        t = inicio_janela
        while t + duracao <= fim_janela:
            fim = t + duracao
            if t >= minimo and not any(t < oc_fim and fim > oc_ini for oc_ini, oc_fim in ocupados):
                livres.append(t)
            t += passo
    return sorted(set(livres))


def janelas_do_dia(dentista, data):
    """Janelas de atendimento do dentista num dia, a partir de HorarioAtendimento."""
    tz = timezone.get_current_timezone()
    return [
        (timezone.make_aware(datetime.combine(data, h.inicio), tz), timezone.make_aware(datetime.combine(data, h.fim), tz))
        for h in dentista.horarios.filter(dia_semana=data.weekday())
    ]


def subtrair_intervalo(intervalos, remover):
    """Remove `remover` de cada intervalo (pode partir um intervalo em dois)."""
    r_ini, r_fim = remover
    resultado = []
    for ini, fim in intervalos:
        if fim <= r_ini or ini >= r_fim:
            resultado.append((ini, fim))
            continue
        if ini < r_ini:
            resultado.append((ini, r_ini))
        if fim > r_fim:
            resultado.append((r_fim, fim))
    return resultado


def _agendamentos_locais(dentista, inicio, fim, ignorar=None):
    """Consultas já registradas no sistema (ainda que o evento no Google falhe)."""
    qs = Agendamento.objects.filter(dentista=dentista, inicio__lt=fim, fim__gt=inicio).exclude(
        status=Agendamento.Status.CANCELADO
    )
    if ignorar is not None:
        qs = qs.exclude(pk=ignorar.pk)
    return list(qs.values_list("inicio", "fim"))


def buscar_horarios_livres(dentista, procedimento, data_inicial, dias=1, agora=None, ignorar=None):
    """Horários livres do dentista para o procedimento, de data_inicial por `dias` dias.

    Retorna {date: [datetime, ...]} só com os dias que têm horário.
    `ignorar`: agendamento sendo remarcado — o horário dele conta como livre.
    Lança ErroGoogle se a agenda não puder ser lida — nunca ofereça horário sem conferir a agenda.
    """
    dias = max(1, min(dias, MAX_DIAS))
    agora = agora or timezone.now()
    tz = timezone.get_current_timezone()
    inicio = timezone.make_aware(datetime.combine(data_inicial, datetime.min.time()), tz)
    fim = inicio + timedelta(days=dias)

    conta = getattr(dentista.unidade, "conta_google", None)
    if conta is None or not conta.token_json:
        raise ErroGoogle(f"A unidade {dentista.unidade} não tem conta Google conectada.")
    calendario = dentista.google_calendar_id or "primary"
    ocupados = GoogleAgenda(conta).ocupados([calendario], inicio, fim).get(calendario, [])
    if ignorar is not None:
        # O freeBusy não diz qual evento ocupa o quê; tira-se o intervalo da própria consulta.
        ocupados = subtrair_intervalo(ocupados, (ignorar.inicio, ignorar.fim))
    ocupados += _agendamentos_locais(dentista, inicio, fim, ignorar=ignorar)

    resultado = {}
    for i in range(dias):
        data = data_inicial + timedelta(days=i)
        livres = calcular_horarios_livres(janelas_do_dia(dentista, data), procedimento.duracao_minutos, ocupados, agora)
        if livres:
            resultado[data] = [h.astimezone(tz) for h in livres]
    return resultado
