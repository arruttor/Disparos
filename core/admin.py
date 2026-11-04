from django.contrib import admin
from django.conf import settings
from .admin_policy import SomenteLegadoAdmin

from .models import Clinica, Dentista, HorarioAtendimento, Procedimento, RegistroLog, Unidade


class UnidadeInline(admin.TabularInline):
    model = Unidade
    extra = 0


@admin.register(Clinica)
class ClinicaAdmin(admin.ModelAdmin):
    inlines = [UnidadeInline]


class DentistaInline(admin.TabularInline):
    model = Dentista
    extra = 0
    fields = ["nome", "google_calendar_id", "ativo"]


@admin.register(Unidade)
class UnidadeAdmin(admin.ModelAdmin):
    list_display = ["nome", "cidade", "clinica", "ativa"]
    inlines = [DentistaInline]

    def get_inlines(self, request, obj=None):
        return [] if settings.MODO_DISPAROS else super().get_inlines(request, obj)

    def get_fields(self, request, obj=None):
        return ['clinica', 'nome', 'cidade', 'endereco', 'ativa'] if settings.MODO_DISPAROS else super().get_fields(request, obj)


class HorarioInline(admin.TabularInline):
    model = HorarioAtendimento
    extra = 0


@admin.register(Dentista)
class DentistaAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    list_display = ["nome", "unidade", "ativo"]
    list_filter = ["unidade", "ativo"]
    filter_horizontal = ["procedimentos"]
    inlines = [HorarioInline]


@admin.register(Procedimento)
class ProcedimentoAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    list_display = ["nome", "duracao_minutos", "urgencia", "ativo"]
    list_filter = ["urgencia", "ativo"]


@admin.register(RegistroLog)
class RegistroLogAdmin(admin.ModelAdmin):
    list_display = ["criado_em", "nivel", "origem", "mensagem"]
    list_filter = ["nivel", "origem"]
    search_fields = ["mensagem", "detalhe"]
