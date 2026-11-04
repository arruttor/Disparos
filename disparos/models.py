from django.conf import settings
from django.db import models


class ConfiguracaoDisparo(models.Model):
    unidade = models.ForeignKey('core.Unidade', on_delete=models.PROTECT, null=True, blank=True)
    mensagem = models.TextField(blank=True)
    ativo = models.BooleanField(default=False)
    horarios = models.CharField(max_length=150, default='09:00,14:00,17:00')
    tamanho_lote = models.PositiveSmallIntegerField(default=10)
    intervalo_segundos = models.PositiveIntegerField(default=60)
    proxima_tentativa_em = models.DateTimeField(null=True, blank=True)
    ultima_execucao = models.DateTimeField(null=True, blank=True)

    @classmethod
    def obter(cls):
        return cls.objects.get_or_create(pk=1)[0]


class Contato(models.Model):
    unidade = models.ForeignKey('core.Unidade', on_delete=models.PROTECT)
    id_origem = models.CharField(max_length=100)
    nome = models.CharField(max_length=250)
    telefone_original = models.CharField(max_length=100, blank=True)
    telefone = models.CharField(max_length=20, blank=True)
    chave_numero = models.CharField(max_length=20, blank=True, db_index=True)
    grupo = models.CharField(max_length=150, blank=True)
    prontuario = models.CharField(max_length=100, blank=True)
    situacao_origem = models.CharField(max_length=100, blank=True)
    selecionado = models.BooleanField(default=True)
    revisao = models.CharField(max_length=255, blank=True)
    automatico_reservado_em = models.DateTimeField(null=True, blank=True, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['pk']
        constraints = [models.UniqueConstraint(fields=['unidade', 'id_origem'], name='disparo_contato_origem_unico')]


class Rodada(models.Model):
    dia = models.DateField()
    horario = models.CharField(max_length=5)
    reservados = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['dia', 'horario'], name='disparo_rodada_unica')]


class Tentativa(models.Model):
    class Status(models.TextChoices):
        RESERVADO = 'reservado', 'Em processamento / resultado pendente'
        ENVIADO = 'enviado', 'Aceito pela Evolution'
        FALHA = 'falha', 'Falha na validação'
        INCERTO = 'incerto', 'Envio com resultado incerto'
        BLOQUEADO = 'bloqueado', 'Bloqueado por duplicidade'
        PAUSADO = 'pausado', 'Interrompido antes do envio'

    contato = models.ForeignKey(Contato, on_delete=models.PROTECT, related_name='tentativas')
    unidade = models.ForeignKey('core.Unidade', on_delete=models.PROTECT)
    instancia = models.CharField(max_length=150)
    nome = models.CharField(max_length=250)
    telefone_original = models.CharField(max_length=100)
    telefone_usado = models.CharField(max_length=20, blank=True)
    mensagem = models.TextField()
    manual = models.BooleanField(default=False)
    autor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    rodada = models.ForeignKey(Rodada, on_delete=models.PROTECT, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RESERVADO)
    detalhe = models.CharField(max_length=255, blank=True)
    id_externo = models.CharField(max_length=150, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    chamada_iniciada_em = models.DateTimeField(null=True, blank=True)
    finalizado_em = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-pk']


class NumeroReservado(models.Model):
    """Livro permanente: nunca apagar nem liberar após falha ou reimportação."""
    chave = models.CharField(max_length=20, unique=True)
    contato = models.ForeignKey(Contato, on_delete=models.PROTECT)
    criado_em = models.DateTimeField(auto_now_add=True)
