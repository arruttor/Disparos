from django.conf import settings


class SomenteLegadoAdmin:
    """Mantém rotas para regressão, mas nega acesso ao admin das funções desativadas."""
    def has_module_permission(self, request):
        return not settings.MODO_DISPAROS and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return not settings.MODO_DISPAROS and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return not settings.MODO_DISPAROS and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return not settings.MODO_DISPAROS and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not settings.MODO_DISPAROS and super().has_delete_permission(request, obj)
