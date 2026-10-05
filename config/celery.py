"""Fila de tarefas (Celery + Redis).

Opcional: sem `CELERY_BROKER_URL` no .env o sistema funciona igual — o assistente usa threads e os
lembretes saem pelo botão do painel ou pelo comando `manage.py enviar_lembretes`.

Rodar (Windows exige `-P threads`; em Linux pode ser o padrão):
    celery -A config worker -P threads -c 4 -l info
    celery -A config beat -l info
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("clinica")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
