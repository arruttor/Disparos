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
    path("integracoes/google/callback/", views.google_callback, name="google_callback"),
    path("integracoes/google/<int:unidade_id>/", views.google_unidade, name="google_unidade"),
    path("integracoes/google/<int:unidade_id>/conectar/", views.google_conectar, name="google_conectar"),
    path("integracoes/google/<int:unidade_id>/desconectar/", views.google_desconectar, name="google_desconectar"),
    path("integracoes/google/<int:unidade_id>/marcar/", views.consulta_marcar, name="consulta_marcar"),
    path("consultas/<int:agendamento_id>/confirmar/", views.consulta_confirmar, name="consulta_confirmar"),
    path("consultas/<int:agendamento_id>/cancelar/", views.consulta_cancelar, name="consulta_cancelar"),
    path("consultas/<int:agendamento_id>/remarcar/", views.consulta_remarcar, name="consulta_remarcar"),
    path("conversas/", views.conversas, name="conversas"),
    path("conversas/assinatura/", views.conversas_assinatura, name="conversas_assinatura"),
    path("conversas/<int:conversa_id>/atualizacoes/", views.conversa_atualizacoes, name="conversa_atualizacoes"),
    path("conversas/<int:conversa_id>/responder/", views.conversa_responder, name="conversa_responder"),
    path("conversas/<int:conversa_id>/nota/", views.conversa_nota, name="conversa_nota"),
    path("conversas/<int:conversa_id>/assumir/", views.conversa_assumir, name="conversa_assumir"),
    path("conversas/<int:conversa_id>/devolver/", views.conversa_devolver, name="conversa_devolver"),
    path("conversas/<int:conversa_id>/encerrar/", views.conversa_encerrar, name="conversa_encerrar"),
    path("assistente/", views.assistente, name="assistente"),
    path("assistente/simular/", views.assistente_simular, name="assistente_simular"),
    path("assistente/lembretes/enviar/", views.assistente_lembretes_agora, name="assistente_lembretes_agora"),
    path("assistente/reiniciar/", views.assistente_reiniciar, name="assistente_reiniciar"),
    path("logs/", views.logs, name="logs"),
]
