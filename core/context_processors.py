from .models import Clinica


def clinica(request):
    """Disponibiliza a clínica em todos os templates (nome na marca do painel)."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {"clinica": Clinica.objects.only("nome").first()}
