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
        fields = ["ia_api_key", "ia_modelo"]


class FormGoogle(FormSegredosMixin, forms.ModelForm):
    campos_secretos = ("google_client_secret",)

    class Meta:
        model = Configuracao
        fields = ["google_client_id", "google_client_secret"]


class FormNumeroWhatsApp(forms.Form):
    numero = forms.CharField(
        max_length=20,
        required=False,
        help_text="Com DDI e DDD, só números. Ex.: 5581999999999",
    )
