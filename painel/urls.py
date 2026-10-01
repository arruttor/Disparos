from django.urls import path

from . import views

app_name = "painel"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("integracoes/", views.integracoes, name="integracoes"),
    path("integracoes/verificar/", views.verificar_tudo, name="verificar_tudo"),
    path("integracoes/evolution/testar/", views.testar_evolution, name="testar_evolution"),
    path("integracoes/ia/testar/", views.testar_ia, name="testar_ia"),
    path("integracoes/whatsapp/<int:unidade_id>/conectar/", views.whatsapp_conectar, name="whatsapp_conectar"),
    path("integracoes/whatsapp/<int:unidade_id>/qrcode/", views.whatsapp_qrcode, name="whatsapp_qrcode"),
    path("integracoes/whatsapp/<int:unidade_id>/status/", views.whatsapp_status, name="whatsapp_status"),
    path("integracoes/whatsapp/<int:unidade_id>/desconectar/", views.whatsapp_desconectar, name="whatsapp_desconectar"),
    path("integracoes/whatsapp/<int:unidade_id>/robo/", views.whatsapp_robo, name="whatsapp_robo"),
    path("logs/", views.logs, name="logs"),
]
