"""Envia os pedidos de confirmação (véspera) e lembretes do dia pendentes.

Uso: python manage.py enviar_lembretes
Sem Celery, dá para agendar este comando no cron (Linux) ou no Agendador de Tarefas (Windows), a cada 5–10 min.
"""

from django.core.management.base import BaseCommand

from atendimento.lembretes import executar


class Command(BaseCommand):
    help = "Envia confirmações da véspera e lembretes do dia pendentes (respeita a janela 8h–20h)."

    def handle(self, *args, **opts):
        r = executar()
        if r.motivo:
            self.stdout.write(r.motivo)
        for d in r.detalhes:
            self.stdout.write(f"  • {d}")
        self.stdout.write(self.style.SUCCESS(
            f"{r.confirmacoes} confirmação(ões), {r.lembretes} lembrete(s), {r.falhas} falha(s)."
        ))
