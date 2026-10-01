from django.db import models


class Clinica(models.Model):
    nome = models.CharField(max_length=150)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "clínica"

    def __str__(self):
        return self.nome


class Unidade(models.Model):
    clinica = models.ForeignKey(Clinica, on_delete=models.CASCADE, related_name="unidades")
    nome = models.CharField(max_length=100)
    cidade = models.CharField(max_length=100, blank=True)
    endereco = models.CharField(max_length=255, blank=True)
    telefone_alerta = models.CharField(
        "telefone para alertas",
        max_length=20,
        blank=True,
        help_text="WhatsApp que recebe aviso quando uma conversa é transferida para humano.",
    )
    ativa = models.BooleanField(default=True)

    def __str__(self):
        return self.nome


class Dentista(models.Model):
    unidade = models.ForeignKey(Unidade, on_delete=models.CASCADE, related_name="dentistas")
    nome = models.CharField(max_length=150)
    google_calendar_id = models.CharField(
        "ID do calendário Google",
        max_length=255,
        blank=True,
        help_text="Calendário onde as consultas deste dentista são marcadas. Vazio = calendário principal da conta.",
    )
    procedimentos = models.ManyToManyField("Procedimento", related_name="dentistas", blank=True)
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return self.nome


class Procedimento(models.Model):
    clinica = models.ForeignKey(Clinica, on_delete=models.CASCADE, related_name="procedimentos")
    nome = models.CharField(max_length=100)
    duracao_minutos = models.PositiveIntegerField(default=30)
    descricao = models.TextField(
        blank=True,
        help_text="Explicação usada pela IA para identificar o procedimento a partir do relato do paciente.",
    )
    urgencia = models.BooleanField(default=False, help_text="Usado para encaixes de urgência.")
    ativo = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.nome} ({self.duracao_minutos} min)"


class HorarioAtendimento(models.Model):
    DIAS = [
        (0, "Segunda"),
        (1, "Terça"),
        (2, "Quarta"),
        (3, "Quinta"),
        (4, "Sexta"),
        (5, "Sábado"),
        (6, "Domingo"),
    ]

    dentista = models.ForeignKey(Dentista, on_delete=models.CASCADE, related_name="horarios")
    dia_semana = models.IntegerField(choices=DIAS)
    inicio = models.TimeField()
    fim = models.TimeField()

    class Meta:
        verbose_name = "horário de atendimento"
        verbose_name_plural = "horários de atendimento"
        ordering = ["dentista", "dia_semana", "inicio"]

    def __str__(self):
        return f"{self.dentista} - {self.get_dia_semana_display()} {self.inicio:%H:%M}-{self.fim:%H:%M}"


class RegistroLog(models.Model):
    NIVEIS = [(n, n) for n in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")]

    criado_em = models.DateTimeField(auto_now_add=True, db_index=True)
    nivel = models.CharField(max_length=10, choices=NIVEIS, db_index=True)
    origem = models.CharField(max_length=100, db_index=True)
    mensagem = models.TextField()
    detalhe = models.TextField(blank=True)

    class Meta:
        verbose_name = "registro de log"
        verbose_name_plural = "registros de log"
        ordering = ["-criado_em"]

    def __str__(self):
        return f"[{self.nivel}] {self.origem}: {self.mensagem[:60]}"
