from django.core.validators import RegexValidator
from django.db import models

from core.fields import CampoCriptografado
from core.models import Unidade


class StatusConexao(models.TextChoices):
    NAO_CONFIGURADO = "nao_configurado", "Não configurado"
    CONECTADO = "conectado", "Conectado"
    AGUARDANDO = "aguardando", "Aguardando conexão"
    ERRO = "erro", "Erro"
    DESCONECTADO = "desconectado", "Desconectado"


class Configuracao(models.Model):
    """Configuração global do sistema (registro único)."""

    # Evolution API
    # CharField (e não URLField) para aceitar nomes de serviço do Docker, ex.: http://evolution:8080
    evolution_url = models.CharField(
        "URL da Evolution API",
        max_length=255,
        blank=True,
        validators=[RegexValidator(r"^https?://\S+$", "Informe uma URL começando com http:// ou https://")],
        help_text="Ex.: http://localhost:8080",
    )
    evolution_api_key = CampoCriptografado("API key da Evolution", blank=True)

    # Google (credenciais OAuth do projeto no Google Cloud)
    google_client_id = models.CharField(max_length=255, blank=True)
    google_client_secret = CampoCriptografado(blank=True)

    # IA
    class Provedor(models.TextChoices):
        ANTHROPIC = "anthropic", "Anthropic (Claude)"
        GEMINI = "gemini", "Google Gemini"
        COMPATIVEL = "compativel", "Outro compatível com OpenAI (Groq, OpenRouter…)"

    ia_provedor = models.CharField("provedor da IA", max_length=20, choices=Provedor.choices, default=Provedor.ANTHROPIC)
    ia_api_key = CampoCriptografado("API key da IA", blank=True)
    ia_modelo = models.CharField("modelo da IA", max_length=100, default="claude-opus-5-5")
    ia_modelo_reserva = models.CharField(
        "modelo reserva", max_length=100, blank=True,
        help_text="Usado quando o modelo principal está sobrecarregado (erro 503) ou sem cota. "
                  "Em branco: Gemini usa gemini-3.5-flash; os demais, nenhum.",
    )
    ia_base_url = models.CharField(
        "URL da API (só para “outro compatível”)", max_length=255, blank=True,
        help_text="Ex.: https://api.groq.com/openai/v1",
    )
    ia_status = models.CharField(max_length=20, choices=StatusConexao.choices, default=StatusConexao.NAO_CONFIGURADO)
    ia_ultima_verificacao = models.DateTimeField(null=True, blank=True)
    ia_detalhe_status = models.CharField(max_length=255, blank=True)

    # Assistente (como ele se apresenta e o que mais precisa saber)
    class Tratamento(models.TextChoices):
        VOCE = "voce", "Você (próximo e cordial)"
        SENHOR = "senhor", "O senhor / a senhora (formal)"

    assistente_nome = models.CharField(
        "nome do assistente", max_length=60, blank=True,
        help_text="Ex.: Clara. Em branco, ele se apresenta como “assistente virtual da clínica”.",
    )
    assistente_tratamento = models.CharField(
        "tratamento", max_length=10, choices=Tratamento.choices, default=Tratamento.VOCE
    )
    assistente_instrucoes = models.TextField(
        "informações e instruções extras", blank=True,
        help_text="Endereços, estacionamento, formas de pagamento, regras da clínica… "
                  "Tudo o que o assistente pode responder. Ele nunca informa preços.",
    )
    espera_resposta_segundos = models.PositiveIntegerField(
        "espera antes de responder (segundos)", default=8,
        help_text="Pacientes costumam mandar várias mensagens seguidas; o assistente espera e responde tudo junto.",
    )

    # Confirmação na véspera e lembrete no dia
    confirmacao_ativa = models.BooleanField(
        "pedir confirmação na véspera", default=True,
        help_text="Manda mensagem no dia anterior pedindo para o paciente confirmar a consulta.",
    )
    confirmacao_hora = models.TimeField(
        "a partir de que horas enviar a confirmação", default="10:00",
        help_text="Na véspera da consulta. Envios só acontecem entre 8h e 20h.",
    )
    lembrete_horas_antes = models.PositiveIntegerField(
        "lembrete no dia (horas antes)", default=2,
        help_text="Lembrete curto no dia da consulta. 0 = não enviar.",
    )

    # Comportamento do robô
    pausa_robo_minutos = models.PositiveIntegerField(
        "pausa do robô (minutos)",
        default=120,
        help_text="Quando um humano responde pelo celular, o robô fica pausado nessa conversa por esse tempo.",
    )

    class Meta:
        verbose_name = "configuração"
        verbose_name_plural = "configuração"

    def __str__(self):
        return "Configuração do sistema"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def obter(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class InstanciaWhatsApp(models.Model):
    """Um número de WhatsApp conectado via Evolution API (um por unidade)."""

    unidade = models.OneToOneField(Unidade, on_delete=models.CASCADE, related_name="whatsapp")
    nome_instancia = models.SlugField(
        max_length=60, unique=True, help_text="Identificador da instância na Evolution (sem espaços)."
    )
    numero = models.CharField(max_length=20, blank=True, help_text="Com DDI e DDD, só números. Ex.: 5581999999999")
    status = models.CharField(max_length=20, choices=StatusConexao.choices, default=StatusConexao.NAO_CONFIGURADO)
    ultima_verificacao = models.DateTimeField(null=True, blank=True)
    detalhe_status = models.CharField(max_length=255, blank=True)
    robo_ativo = models.BooleanField(default=True, help_text="Desligue para o robô parar de responder neste número.")

    class Meta:
        verbose_name = "instância de WhatsApp"
        verbose_name_plural = "instâncias de WhatsApp"

    def __str__(self):
        return f"{self.unidade} ({self.numero or self.nome_instancia})"


class ContaGoogle(models.Model):
    """Conta Google conectada via OAuth para acesso à agenda (uma por unidade)."""

    unidade = models.OneToOneField(Unidade, on_delete=models.CASCADE, related_name="conta_google")
    email = models.EmailField(blank=True)
    token_json = CampoCriptografado(blank=True, help_text="Credenciais OAuth (gerado automaticamente).")
    status = models.CharField(max_length=20, choices=StatusConexao.choices, default=StatusConexao.NAO_CONFIGURADO)
    ultima_verificacao = models.DateTimeField(null=True, blank=True)
    detalhe_status = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = "conta Google"
        verbose_name_plural = "contas Google"

    def __str__(self):
        return f"{self.unidade} ({self.email or 'não conectada'})"
