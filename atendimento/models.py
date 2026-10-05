from django.conf import settings
from django.db import models

from core.models import Clinica, Dentista, Procedimento, Unidade


class Paciente(models.Model):
    clinica = models.ForeignKey(Clinica, on_delete=models.CASCADE, related_name="pacientes")
    nome = models.CharField(max_length=150, blank=True)
    telefone = models.CharField(max_length=20, help_text="Com DDI e DDD, só números.")
    data_nascimento = models.DateField(null=True, blank=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["clinica", "telefone"], name="paciente_telefone_unico")]

    def __str__(self):
        return self.nome or self.telefone


class Conversa(models.Model):
    class Status(models.TextChoices):
        ROBO = "robo", "Com o assistente"
        HUMANO = "humano", "Com a equipe"
        ENCERRADA = "encerrada", "Encerrada"

    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="conversas")
    unidade = models.ForeignKey(Unidade, on_delete=models.CASCADE, related_name="conversas")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ROBO)
    robo_pausado_ate = models.DateTimeField(null=True, blank=True)
    motivo_transferencia = models.CharField(max_length=255, blank=True)
    atendente = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="conversas",
        help_text="Quem da equipe assumiu a conversa.",
    )
    simulada = models.BooleanField(
        default=False, help_text="Conversa do simulador do painel: nada é enviado pelo WhatsApp."
    )
    # Resumo da última mensagem (mantido por signal) — deixa a caixa de entrada rápida.
    ultima_mensagem_em = models.DateTimeField(null=True, blank=True, db_index=True)
    ultima_mensagem_origem = models.CharField(max_length=10, blank=True)
    ultima_mensagem_texto = models.CharField(max_length=140, blank=True)
    iniciada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-atualizada_em"]

    CAMPOS_RESUMO = ("ultima_mensagem_em", "ultima_mensagem_origem", "ultima_mensagem_texto")

    def save(self, *args, **kwargs):
        # O resumo é mantido pelo signal com UPDATE direto. Um save() completo de um objeto
        # carregado antes da última mensagem regravaria o resumo antigo — por isso fica de fora.
        if self.pk and kwargs.get("update_fields") is None and not kwargs.get("force_insert"):
            kwargs["update_fields"] = [
                f.name for f in self._meta.concrete_fields if not f.primary_key and f.name not in self.CAMPOS_RESUMO
            ]
        super().save(*args, **kwargs)

    @property
    def aguardando_resposta(self):
        """Com a equipe e a última palavra é do paciente."""
        return self.status == self.Status.HUMANO and self.ultima_mensagem_origem == Mensagem.Origem.PACIENTE

    def __str__(self):
        return f"{self.paciente} @ {self.unidade}"


class Mensagem(models.Model):
    class Origem(models.TextChoices):
        PACIENTE = "paciente", "Paciente"
        ROBO = "robo", "Assistente"
        HUMANO = "humano", "Equipe"
        NOTA = "nota", "Nota interna"  # só a equipe vê; não vai ao paciente nem à IA

    conversa = models.ForeignKey(Conversa, on_delete=models.CASCADE, related_name="mensagens")
    origem = models.CharField(max_length=10, choices=Origem.choices)
    texto = models.TextField()
    autor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text="Quem escreveu pelo painel (resposta da equipe ou nota).",
    )
    id_externo = models.CharField(max_length=100, blank=True, db_index=True, help_text="ID da mensagem no WhatsApp.")
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "mensagens"
        ordering = ["criada_em"]

    def __str__(self):
        return f"{self.get_origem_display()}: {self.texto[:50]}"


class Agendamento(models.Model):
    class Status(models.TextChoices):
        AGUARDANDO = "aguardando", "Aguardando confirmação"
        CONFIRMADO = "confirmado", "Confirmado"
        CANCELADO = "cancelado", "Cancelado"

    class Origem(models.TextChoices):
        ASSISTENTE = "assistente", "Assistente (WhatsApp)"
        PAINEL = "painel", "Painel"

    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="agendamentos")
    dentista = models.ForeignKey(Dentista, on_delete=models.PROTECT, related_name="agendamentos")
    procedimento = models.ForeignKey(Procedimento, on_delete=models.PROTECT, related_name="agendamentos")
    inicio = models.DateTimeField(db_index=True)
    fim = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AGUARDANDO)
    origem = models.CharField(max_length=20, choices=Origem.choices, default=Origem.PAINEL)
    # Calendário onde o evento foi criado. Guardado porque o dentista pode trocar de
    # calendário depois — as atualizações precisam ir para o evento original.
    google_calendar_id = models.CharField(max_length=255, blank=True)
    google_event_id = models.CharField(max_length=255, blank=True)
    # Pedido de confirmação da véspera (D-1) e lembrete do dia — preenchidos quando enviados.
    lembrete_enviado_em = models.DateTimeField("pedido de confirmação enviado em", null=True, blank=True)
    aviso_dia_enviado_em = models.DateTimeField("lembrete do dia enviado em", null=True, blank=True)
    confirmado_em = models.DateTimeField(null=True, blank=True)
    cancelado_em = models.DateTimeField(null=True, blank=True)
    motivo_cancelamento = models.CharField(max_length=255, blank=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["inicio"]

    def __str__(self):
        return f"{self.paciente} - {self.procedimento.nome} em {self.inicio:%d/%m %H:%M}"
