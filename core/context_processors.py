from .models import Clinica


def clinica(request):
    """Disponibiliza a clínica (nome na marca — white-label) e o contador da caixa de entrada."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    from atendimento.models import Conversa, Mensagem  # import tardio: core não depende de atendimento no carregamento

    aguardando = Conversa.objects.filter(
        simulada=False, status=Conversa.Status.HUMANO, ultima_mensagem_origem=Mensagem.Origem.PACIENTE
    ).count()
    return {"clinica": Clinica.objects.only("nome").first(), "conversas_aguardando": aguardando}
