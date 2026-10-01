"""Configurações do projeto. Valores sensíveis vêm do arquivo .env."""

import os
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

# URL pública do sistema (usada para webhooks e callback do OAuth do Google).
URL_PUBLICA = os.getenv("URL_PUBLICA", "http://localhost:8000").rstrip("/")

# Token que a Evolution envia na URL do webhook (impede chamadas de terceiros).
WEBHOOK_TOKEN = env_obrigatoria("WEBHOOK_TOKEN")

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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
STATIC_ROOT = BASE_DIR / "staticfiles"

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
