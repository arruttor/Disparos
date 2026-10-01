from django.contrib import admin

from .models import Agendamento, Conversa, Mensagem, Paciente


@admin.register(Paciente)
class PacienteAdmin(admin.ModelAdmin):
    list_display = ["nome", "telefone", "criado_em"]
    search_fields = ["nome", "telefone"]


class MensagemInline(admin.TabularInline):
    model = Mensagem
    extra = 0
    readonly_fields = ["origem", "texto", "criada_em"]
    can_delete = False


@admin.register(Conversa)
class ConversaAdmin(admin.ModelAdmin):
    list_display = ["paciente", "unidade", "status", "atualizada_em"]
    list_filter = ["status", "unidade"]
    inlines = [MensagemInline]


@admin.register(Agendamento)
class AgendamentoAdmin(admin.ModelAdmin):
    list_display = ["paciente", "dentista", "procedimento", "inicio", "status"]
    list_filter = ["status", "dentista"]
    date_hierarchy = "inicio"
