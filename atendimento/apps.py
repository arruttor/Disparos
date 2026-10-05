from django.apps import AppConfig


class AtendimentoConfig(AppConfig):
    name = 'atendimento'
    verbose_name = 'Atendimento'

    def ready(self):
        from . import signals  # noqa: F401  (registra os receivers)
