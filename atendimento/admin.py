import logging

from django.contrib import admin, messages
from core.admin_policy import SomenteLegadoAdmin

from .models import Agendamento, Conversa, Mensagem, Paciente

logger = logging.getLogger("clinica.painel")


@admin.register(Paciente)
class PacienteAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    list_display = ["nome", "telefone", "criado_em"]
    search_fields = ["nome", "telefone"]


class MensagemInline(admin.TabularInline):
    model = Mensagem
    extra = 0
    readonly_fields = ["origem", "texto", "criada_em"]
    can_delete = False


@admin.action(description="Devolver ao assistente (ele volta a responder)")
def devolver_ao_assistente(modeladmin, request, queryset):
    total = queryset.exclude(status=Conversa.Status.ROBO).update(
        status=Conversa.Status.ROBO, robo_pausado_ate=None, motivo_transferencia="", atendente=None
    )
    logger.info("%d conversa(s) devolvida(s) ao assistente por %s.", total, request.user)
    modeladmin.message_user(request, f"{total} conversa(s) devolvida(s) ao assistente.", messages.SUCCESS)


@admin.register(Conversa)
class ConversaAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    list_display = ["paciente", "unidade", "status", "motivo_transferencia", "simulada", "atualizada_em"]
    list_filter = ["status", "unidade", "simulada"]
    actions = [devolver_ao_assistente]
    inlines = [MensagemInline]


@admin.register(Agendamento)
class AgendamentoAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    list_display = ["paciente", "dentista", "procedimento", "inicio", "status"]
    list_filter = ["status", "dentista"]
    date_hierarchy = "inicio"
