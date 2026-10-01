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
        ROBO = "robo", "Com o robô"
        HUMANO = "humano", "Aguardando humano"
        ENCERRADA = "encerrada", "Encerrada"

    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="conversas")
    unidade = models.ForeignKey(Unidade, on_delete=models.CASCADE, related_name="conversas")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ROBO)
    robo_pausado_ate = models.DateTimeField(null=True, blank=True)
    motivo_transferencia = models.CharField(max_length=255, blank=True)
    iniciada_em = models.DateTimeField(auto_now_add=True)
    atualizada_em = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-atualizada_em"]

    def __str__(self):
        return f"{self.paciente} @ {self.unidade}"


class Mensagem(models.Model):
    class Origem(models.TextChoices):
        PACIENTE = "paciente", "Paciente"
        ROBO = "robo", "Robô"
        HUMANO = "humano", "Humano"

    conversa = models.ForeignKey(Conversa, on_delete=models.CASCADE, related_name="mensagens")
    origem = models.CharField(max_length=10, choices=Origem.choices)
    texto = models.TextField()
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

    paciente = models.ForeignKey(Paciente, on_delete=models.CASCADE, related_name="agendamentos")
    dentista = models.ForeignKey(Dentista, on_delete=models.PROTECT, related_name="agendamentos")
    procedimento = models.ForeignKey(Procedimento, on_delete=models.PROTECT, related_name="agendamentos")
    inicio = models.DateTimeField()
    fim = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.AGUARDANDO)
    google_event_id = models.CharField(max_length=255, blank=True)
    lembrete_enviado_em = models.DateTimeField(null=True, blank=True)
    observacoes = models.TextField(blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["inicio"]

    def __str__(self):
        return f"{self.paciente} - {self.procedimento.nome} em {self.inicio:%d/%m %H:%M}"
