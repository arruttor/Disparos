"""Rotas históricas, usadas exclusivamente pelos testes de regressão."""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "Clínica - Administração"
admin.site.site_title = "Clínica"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("entrar/", auth_views.LoginView.as_view(template_name="painel/login.html"), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("integracoes/", include("integracoes.urls")),
    path("", include("painel.urls")),
]
