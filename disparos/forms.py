from django import forms
from django.core.validators import MinValueValidator, MaxValueValidator
from core.models import Unidade
from .models import ConfiguracaoDisparo, Contato
from .services import horarios_validos, validar_mensagem
from .telefones import chave, normalizar


class FormConfiguracao(forms.ModelForm):
    class Meta:
        model = ConfiguracaoDisparo
        fields = ['unidade', 'mensagem', 'horarios', 'tamanho_lote', 'intervalo_segundos']
        labels = {'unidade': 'WhatsApp da unidade', 'mensagem': 'Mensagem inicial', 'horarios': 'Horários dos lotes', 'tamanho_lote': 'Pacientes por lote', 'intervalo_segundos': 'Intervalo mínimo entre tentativas (segundos)'}
        help_texts = {'mensagem': 'Use {nome} para o nome completo ou {primeiro_nome}.', 'horarios': 'Horário de Brasília. Janela de uma hora por lote. Ex.: 09:00,14:00,17:00.', 'tamanho_lote': 'Entre 1 e 50. Novos horários e mensagens não liberam números já tentados.', 'intervalo_segundos': 'No mínimo 60 segundos. Uma tentativa por execução do agendador.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unidade'].required = self.fields['mensagem'].required = True
        self.fields['unidade'].queryset = Unidade.objects.filter(ativa=True)
        self.fields['tamanho_lote'].min_value, self.fields['tamanho_lote'].max_value = 1, 50
        self.fields['intervalo_segundos'].min_value, self.fields['intervalo_segundos'].max_value = 60, 3600
        for campo, minimo, maximo in (('tamanho_lote', 1, 50), ('intervalo_segundos', 60, 3600)):
            self.fields[campo].validators.extend([MinValueValidator(minimo), MaxValueValidator(maximo)])
            self.fields[campo].widget.attrs.update(min=minimo, max=maximo)

    def clean_mensagem(self):
        try:
            return validar_mensagem(self.cleaned_data['mensagem'])
        except ValueError as e:
            raise forms.ValidationError(str(e)) from e

    def clean_horarios(self):
        try:
            return ','.join(h.strftime('%H:%M') for h in horarios_validos(self.cleaned_data['horarios']))
        except ValueError as e:
            raise forms.ValidationError(str(e)) from e

    def clean(self):
        dados = super().clean()
        lote, intervalo = dados.get('tamanho_lote'), dados.get('intervalo_segundos')
        if lote and intervalo and (lote - 1) * intervalo >= 3600:
            self.add_error('tamanho_lote', 'O lote precisa caber na janela de uma hora.')
        return dados


class FormImportar(forms.Form):
    unidade = forms.ModelChoiceField(queryset=Unidade.objects.filter(ativa=True), label='Unidade dos pacientes')
    arquivo = forms.FileField(label='Planilha (.xlsx ou .csv)', widget=forms.FileInput(attrs={'accept': '.xlsx,.csv'}))


class FormTeste(forms.Form):
    nome = forms.CharField(label='Seu nome para o teste', max_length=250)
    telefone = forms.CharField(label='Seu WhatsApp de teste', max_length=30,
        help_text='O teste é um envio real e também reserva o número permanentemente.')

    def clean_telefone(self):
        numero = normalizar(self.cleaned_data['telefone'])
        if not numero:
            raise forms.ValidationError('Informe DDD e número brasileiro válido.')
        return numero


class FormContato(forms.ModelForm):
    class Meta:
        model = Contato
        fields = ['nome', 'telefone', 'selecionado']
        labels = {'nome': 'Nome do paciente', 'telefone': 'Telefone corrigido (DDD + número)', 'selecionado': 'Incluir na fila automática, se ainda não houve tentativa'}

    def clean_telefone(self):
        valor = self.cleaned_data['telefone']
        if not normalizar(valor):
            raise forms.ValidationError('Informe um telefone brasileiro válido com DDD.')
        return normalizar(valor)

    def save(self, commit=True):
        contato = super().save(commit=False)
        contato.chave_numero = chave(contato.telefone)
        contato.revisao = ''
        if commit:
            contato.save(update_fields=['nome', 'telefone', 'chave_numero', 'selecionado', 'revisao'])
        return contato
