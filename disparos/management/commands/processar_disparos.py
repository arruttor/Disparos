from django.core.management.base import BaseCommand
from disparos.services import executar_rodada


class Command(BaseCommand):
    help = 'Executa um passo da fila, respeitando pausa, horários e reservas permanentes.'

    def handle(self, *args, **options):
        tentativa = executar_rodada()
        self.stdout.write(f'Tentativa {tentativa.pk} processada.' if tentativa else 'Nenhuma tentativa elegível neste momento.')
