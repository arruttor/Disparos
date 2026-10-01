"""Campo de texto criptografado (Fernet) para tokens e chaves de API."""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    chave = settings.CHAVE_CRIPTOGRAFIA
    if not chave:
        raise ImproperlyConfigured(
            "CHAVE_CRIPTOGRAFIA não definida no .env. Gere uma com: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(chave.encode())


class CampoCriptografado(models.TextField):
    """Guarda o valor criptografado no banco e devolve o texto puro no Python."""

    def from_db_value(self, value, expression, connection):
        if not value:
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            return ""

    def get_prep_value(self, value):
        if not value:
            return value
        return _fernet().encrypt(str(value).encode()).decode()
