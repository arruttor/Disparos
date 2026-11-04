"""Configurações do projeto. Valores sensíveis vêm do arquivo .env."""

import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(nome, padrao=False):
    return os.getenv(nome, str(padrao)).lower() in ("1", "true", "sim", "yes")


def env_obrigatoria(nome):
    """Segredos não têm valor padrão: sem o .env o sistema não sobe."""
    valor = os.getenv(nome, "").strip()
    if not valor:
        raise ImproperlyConfigured(f"Variável {nome} não definida. Copie .env.example para .env e preencha.")
    if not DEBUG and valor.startswith("troque"):
        raise ImproperlyConfigured(f"Variável {nome} ainda está com o valor de exemplo do .env.example.")
    return valor


# Padrão seguro: DEBUG só liga se o .env pedir explicitamente.
DEBUG = env_bool("DJANGO_DEBUG", False)
SECRET_KEY = env_obrigatoria("DJANGO_SECRET_KEY")
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# Chave usada para criptografar tokens/chaves de API salvos no banco (Fernet).
CHAVE_CRIPTOGRAFIA = env_obrigatoria("CHAVE_CRIPTOGRAFIA")

# URL pela qual a Evolution chama o webhook (rede interna: host.docker.internal, nome do serviço Docker…).
URL_PUBLICA = os.getenv("URL_PUBLICA", "http://localhost:8000").rstrip("/")

# URL pela qual o NAVEGADOR acessa o painel. Usada no retorno do login do Google, que só
# aceita http://localhost ou https — por isso é separada da URL_PUBLICA.
# Precisa estar cadastrada, idêntica, nas "URIs de redirecionamento" do Google Cloud.
URL_SITE = os.getenv("URL_SITE", "http://localhost:8000").rstrip("/")

# Token que a Evolution envia na URL do webhook (impede chamadas de terceiros).
WEBHOOK_TOKEN = env_obrigatoria("WEBHOOK_TOKEN")

# O assistente responde em segundo plano (não segura o webhook). Nos testes roda direto, sem espera.
ASSISTENTE_EM_SEGUNDO_PLANO = "test" not in sys.argv

# Fila de tarefas (opcional). Com CELERY_BROKER_URL no .env, o assistente e os lembretes vão para o
# Celery; sem ele, o assistente usa threads e os lembretes saem pelo painel/comando.
# Nos testes a fila fica desligada (não depender de Redis); os testes da fila usam override_settings.
CELERY_BROKER_URL = "" if "test" in sys.argv else os.getenv("CELERY_BROKER_URL", "")
CELERY_TIMEZONE = "America/Recife"
CELERY_TASK_ACKS_LATE = True  # tarefa só sai da fila depois de concluída: sobrevive a reinício do worker
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
MODO_DISPAROS = True
CELERY_BEAT_SCHEDULE = {
    "processar-fila-de-disparos": {"task": "disparos.processar_fila", "schedule": 60.0},
}

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "integracoes",
    "atendimento",
    "painel",
    "disparos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.clinica",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# SQLite no desenvolvimento; PostgreSQL em produção (definir DATABASE_* no .env).
if os.getenv("DATABASE_NAME"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DATABASE_NAME"),
            "USER": os.getenv("DATABASE_USER", "postgres"),
            "PASSWORD": os.getenv("DATABASE_PASSWORD", ""),
            "HOST": os.getenv("DATABASE_HOST", "localhost"),
            "PORT": os.getenv("DATABASE_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            # SQLite só aceita uma gravação por vez. Webhook + painel + assistente gravam juntos:
            # WAL deixa leituras não bloquearem gravações, IMMEDIATE reserva a gravação no início da
            # transação (evita impasse) e o timeout maior espera a vez em vez de dar "database is locked".
            "OPTIONS": {
                "timeout": 20,
                "transaction_mode": "IMMEDIATE",
                "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
            },
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Recife"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Em produção os estáticos ganham o hash do conteúdo no nome (painel.a1b2c3.css):
# CSS/JS novos nunca ficam presos no cache do navegador. Exige `collectstatic` a cada deploy.
if not DEBUG:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "painel:dashboard"
LOGOUT_REDIRECT_URL = "login"

# Logs: console + banco de dados (visíveis na tela de Logs do painel).
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simples": {"format": "[{asctime}] {levelname} {name}: {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simples"},
        "banco": {"class": "core.logging_handlers.BancoDeDadosHandler", "level": "INFO"},
    },
    "loggers": {
        "django.request": {"handlers": ["console", "banco"], "level": "ERROR", "propagate": False},
        "clinica": {"handlers": ["console", "banco"], "level": "DEBUG", "propagate": False},
    },
}
