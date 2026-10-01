from django.urls import path

from . import views

app_name = "integracoes"

urlpatterns = [
    path("webhook/evolution/", views.webhook_evolution, name="webhook_evolution"),
]
