from django.contrib import admin
from core.admin_policy import SomenteLegadoAdmin

from .models import Configuracao, ContaGoogle, InstanciaWhatsApp


@admin.register(Configuracao)
class ConfiguracaoAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    exclude = ["ia_status", "ia_ultima_verificacao", "ia_detalhe_status"]

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not Configuracao.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InstanciaWhatsApp)
class InstanciaWhatsAppAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    list_display = ["unidade", "nome_instancia", "numero", "status", "robo_ativo"]
    readonly_fields = ["status", "ultima_verificacao", "detalhe_status"]


@admin.register(ContaGoogle)
class ContaGoogleAdmin(SomenteLegadoAdmin, admin.ModelAdmin):
    list_display = ["unidade", "email", "status"]
    readonly_fields = ["email", "status", "ultima_verificacao", "detalhe_status"]
    exclude = ["token_json"]
