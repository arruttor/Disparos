from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from core.models import Unidade
from disparos.importacao import importar


class Command(BaseCommand):
    help = 'Importa Excel/CSV sem ativar disparos e sem apagar reservas anteriores.'

    def add_arguments(self, parser):
        parser.add_argument('arquivo')
        parser.add_argument('--unidade', type=int, required=True)

    def handle(self, *args, **options):
        try:
            unidade = Unidade.objects.get(pk=options['unidade'], ativa=True)
            with open(options['arquivo'], 'rb') as arquivo:
                resultado = importar(File(arquivo, name=options['arquivo']), unidade)
        except (ValueError, OSError, Unidade.DoesNotExist) as e:
            raise CommandError('Confira arquivo e unidade. Não foi possível importar.') from e
        self.stdout.write(f'Novos: {resultado[0]}; atualizados: {resultado[1]}; revisão: {resultado[2]}.')
