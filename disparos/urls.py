from django.urls import path
from . import views as disparos
from painel import views

app_name = 'painel'
urlpatterns = [
    path('', disparos.inicio, name='dashboard'),
    path('pacientes/', disparos.pacientes, name='pacientes'),
    path('pacientes/selecao/', disparos.selecionar, name='selecionar'),
    path('pacientes/<int:contato_id>/', disparos.contato, name='contato'),
    path('pacientes/<int:contato_id>/tentar/', disparos.tentar_manualmente, name='tentar_manualmente'),
    path('disparos/controle/', disparos.alternar, name='disparos_controle'),
    path('disparos/teste/', disparos.enviar_teste, name='enviar_teste'),
    path('disparos/historico/', disparos.historico, name='historico'),
    path('integracoes/', disparos.integracoes, name='integracoes'),
    path('integracoes/verificar/', disparos.verificar_tudo, name='verificar_tudo'),
    path('integracoes/evolution/testar/', views.testar_evolution, name='testar_evolution'),
    path('integracoes/whatsapp/<int:unidade_id>/conectar/', views.whatsapp_conectar, name='whatsapp_conectar'),
    path('integracoes/whatsapp/<int:unidade_id>/qrcode/', views.whatsapp_qrcode, name='whatsapp_qrcode'),
    path('integracoes/whatsapp/<int:unidade_id>/status/', views.whatsapp_status, name='whatsapp_status'),
    path('integracoes/whatsapp/<int:unidade_id>/desconectar/', views.whatsapp_desconectar, name='whatsapp_desconectar'),
    path('logs/', views.logs, name='logs'),
]
