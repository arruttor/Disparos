"""Cria dados genéricos para desenvolvimento: clínica, unidades, dentista, procedimentos e horários.

Uso: python manage.py dados_iniciais
"""

from datetime import time

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Clinica, Dentista, HorarioAtendimento, Procedimento, Unidade
from integracoes.models import Configuracao

PROCEDIMENTOS = [
    ("Avaliação", 30, False, "Primeira consulta ou check-up. Use quando o paciente não sabe o que precisa ou quer orçamento/valores."),
    ("Limpeza (profilaxia)", 45, False, "Limpeza, remoção de tártaro, manutenção periódica."),
    ("Restauração", 60, False, "Cárie, dente quebrado/lascado sem dor intensa, troca de restauração."),
    ("Tratamento de canal (endodontia)", 90, False, "Indicado pelo dentista após avaliação; dor que persiste, dente escurecido."),
    ("Extração", 60, False, "Remoção de dente, inclusive siso, quando já indicada pelo dentista."),
    ("Clareamento", 60, False, "Clareamento dental estético."),
    ("Manutenção de aparelho", 30, False, "Paciente que já usa aparelho ortodôntico e precisa da manutenção mensal."),
    ("Urgência", 30, True, "Dor forte, inchaço no rosto, sangramento, trauma/pancada, dente quebrado com dor."),
]

# Segunda a sexta 08:00-12:00 e 14:00-18:00; sábado 08:00-12:00.
HORARIOS = [(dia, time(8), time(12)) for dia in range(6)] + [(dia, time(14), time(18)) for dia in range(5)]


class Command(BaseCommand):
    help = "Cria dados genéricos para desenvolvimento (não duplica se já existirem)."

    @transaction.atomic
    def handle(self, *args, **opts):
        Configuracao.obter()
        clinica, _ = Clinica.objects.get_or_create(nome="Clínica Odontológica (exemplo)")

        recife, _ = Unidade.objects.get_or_create(clinica=clinica, nome="Recife", defaults={"cidade": "Recife - PE"})
        Unidade.objects.get_or_create(clinica=clinica, nome="Minas", defaults={"cidade": "Minas Gerais"})

        procedimentos = []
        for nome, duracao, urgencia, descricao in PROCEDIMENTOS:
            p, _ = Procedimento.objects.get_or_create(
                clinica=clinica,
                nome=nome,
                defaults={"duracao_minutos": duracao, "urgencia": urgencia, "descricao": descricao},
            )
            procedimentos.append(p)

        dentista, criado = Dentista.objects.get_or_create(unidade=recife, nome="Dr(a). Exemplo")
        dentista.procedimentos.set(procedimentos)
        if criado:
            for dia, inicio, fim in HORARIOS:
                HorarioAtendimento.objects.create(dentista=dentista, dia_semana=dia, inicio=inicio, fim=fim)

        self.stdout.write(self.style.SUCCESS("Dados iniciais criados/verificados."))
