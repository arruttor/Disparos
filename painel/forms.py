from django import forms

from integracoes.models import Configuracao


class FormSegredosMixin:
    """Campos de segredo: em branco = manter o valor atual."""

    campos_secretos = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nome in self.campos_secretos:
            campo = self.fields[nome]
            campo.required = False
            campo.widget = forms.PasswordInput(render_value=False)
            if getattr(self.instance, nome):
                campo.help_text = "Já definida. Deixe em branco para manter."

    def clean(self):
        dados = super().clean()
        for nome in self.campos_secretos:
            if not dados.get(nome):
                dados[nome] = getattr(self.instance, nome)
        return dados


class FormEvolution(FormSegredosMixin, forms.ModelForm):
    campos_secretos = ("evolution_api_key",)

    class Meta:
        model = Configuracao
        fields = ["evolution_url", "evolution_api_key"]


class FormIA(FormSegredosMixin, forms.ModelForm):
    campos_secretos = ("ia_api_key",)

    class Meta:
        model = Configuracao
        fields = ["ia_provedor", "ia_api_key", "ia_modelo", "ia_modelo_reserva", "ia_base_url"]
        help_texts = {
            "ia_modelo": "Anthropic: claude-opus-5-5 · Gemini: gemini-3.8-flash. Em branco = padrão do provedor.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ia_modelo"].required = False

    def clean(self):
        from integracoes.ia import MODELO_PADRAO

        dados = super().clean()
        provedor = dados.get("ia_provedor")
        modelo = (dados.get("ia_modelo") or "").strip()
        eh_claude = modelo.startswith("claude")
        # Trocou de provedor e ficou o modelo do outro? Usa o padrão do provedor escolhido.
        if not modelo or (provedor == Configuracao.Provedor.ANTHROPIC) != eh_claude:
            modelo = MODELO_PADRAO.get(provedor, "")
        if not modelo:
            self.add_error("ia_modelo", "Informe o nome do modelo do provedor.")
        dados["ia_modelo"] = modelo
        reserva = (dados.get("ia_modelo_reserva") or "").strip()
        if reserva and (provedor == Configuracao.Provedor.ANTHROPIC) != reserva.startswith("claude"):
            dados["ia_modelo_reserva"] = ""  # reserva de outro provedor não serve
        if provedor == Configuracao.Provedor.COMPATIVEL and not (dados.get("ia_base_url") or "").strip():
            self.add_error("ia_base_url", "Informe a URL da API do provedor.")
        return dados


class FormGoogle(FormSegredosMixin, forms.ModelForm):
    campos_secretos = ("google_client_secret",)

    class Meta:
        model = Configuracao
        fields = ["google_client_id", "google_client_secret"]
        labels = {"google_client_id": "Client ID", "google_client_secret": "Client secret"}


class FormAssistente(forms.ModelForm):
    class Meta:
        model = Configuracao
        fields = [
            "assistente_nome", "assistente_tratamento", "assistente_instrucoes",
            "espera_resposta_segundos", "pausa_robo_minutos",
        ]
        widgets = {"assistente_instrucoes": forms.Textarea}
        help_texts = {
            "espera_resposta_segundos": "Quanto o assistente espera depois da última mensagem antes de responder "
                                        "(cada mensagem nova recomeça a contagem). Se a mensagem for só um cumprimento "
                                        "(“oi”, “boa tarde”), ele espera 12 segundos a mais.",
        }


class FormLembretes(forms.ModelForm):
    class Meta:
        model = Configuracao
        fields = ["confirmacao_ativa", "confirmacao_hora", "lembrete_horas_antes"]
        widgets = {"confirmacao_hora": forms.TimeInput(attrs={"type": "time"}, format="%H:%M")}

    def clean_confirmacao_hora(self):
        from atendimento.lembretes import JANELA_ENVIO

        hora = self.cleaned_data["confirmacao_hora"]
        if not (JANELA_ENVIO[0] <= hora < JANELA_ENVIO[1]):
            raise forms.ValidationError("Escolha um horário entre 08:00 e 19:59 — fora disso nada é enviado.")
        return hora

    def clean_lembrete_horas_antes(self):
        horas = self.cleaned_data["lembrete_horas_antes"]
        if horas > 12:
            raise forms.ValidationError("Use no máximo 12 horas (o pedido de confirmação já sai na véspera).")
        return horas

    def clean_espera_resposta_segundos(self):
        valor = self.cleaned_data["espera_resposta_segundos"]
        if valor > 60:
            raise forms.ValidationError("Use no máximo 60 segundos — o paciente fica esperando.")
        return valor


class FormNumeroWhatsApp(forms.Form):
    numero = forms.CharField(
        max_length=20,
        required=False,
        help_text="Com DDI e DDD, só números. Ex.: 5581999999999",
    )
