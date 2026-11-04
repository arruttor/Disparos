import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections
from disparos.services import executar_rodada


class Command(BaseCommand):
    help = 'Processa a fila uma vez ou continuamente, respeitando pausa, horários e reservas permanentes.'

    def add_arguments(self, parser):
        parser.add_argument('--continuo', action='store_true', help='Verifica a fila a cada minuto, sem depender de Celery.')
        parser.add_argument('--exigir-postgres', action='store_true', help='Recusa iniciar com um banco SQLite isolado.')

    def handle(self, *args, **options):
        if options['exigir_postgres'] and settings.DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
            raise CommandError('Configure as cinco variáveis DATABASE_* do mesmo PostgreSQL usado pelo painel.')
        if options['continuo']:
            self.stdout.write('Agendador iniciado: uma verificação por minuto. A pausa e a programação do painel serão respeitadas.')
            self.stdout.flush()
        try:
            while True:
                close_old_connections()
                tentativa = executar_rodada()
                self.stdout.write(f'Tentativa {tentativa.pk} processada.' if tentativa else 'Nenhuma tentativa elegível neste momento.')
                self.stdout.flush()
                if not options['continuo']:
                    break
                # Espera após a execução: não acumula ticks atrasados nem cria rajadas.
                time.sleep(60)
        except KeyboardInterrupt:
            self.stdout.write('Agendador encerrado. Reservas anteriores preservadas.')
        finally:
            close_old_connections()
