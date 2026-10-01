from django import template

register = template.Library()

CORES_STATUS = {
    "conectado": "success",
    "aguardando": "warning",
    "desconectado": "secondary",
    "erro": "danger",
    "nao_configurado": "light text-dark border",
}

CORES_NIVEL = {
    "DEBUG": "light text-dark border",
    "INFO": "info",
    "WARNING": "warning",
    "ERROR": "danger",
    "CRITICAL": "dark",
}


@register.filter
def cor_status(status):
    return CORES_STATUS.get(status, "secondary")


@register.filter
def cor_nivel(nivel):
    return CORES_NIVEL.get(nivel, "secondary")
