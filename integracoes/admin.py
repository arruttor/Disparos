from django.contrib import admin

from .models import Configuracao, ContaGoogle, InstanciaWhatsApp


@admin.register(Configuracao)
class ConfiguracaoAdmin(admin.ModelAdmin):
    exclude = ["ia_status", "ia_ultima_verificacao", "ia_detalhe_status"]

    def has_add_permission(self, request):
        return not Configuracao.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(InstanciaWhatsApp)
class InstanciaWhatsAppAdmin(admin.ModelAdmin):
    list_display = ["unidade", "nome_instancia", "numero", "status", "robo_ativo"]
    readonly_fields = ["status", "ultima_verificacao", "detalhe_status"]


@admin.register(ContaGoogle)
class ContaGoogleAdmin(admin.ModelAdmin):
    list_display = ["unidade", "email", "status"]
    readonly_fields = ["email", "status", "ultima_verificacao", "detalhe_status"]
    exclude = ["token_json"]
