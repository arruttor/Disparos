import csv
import io
import unicodedata
from itertools import islice
from zipfile import BadZipFile, ZipFile

from django.db import transaction

from .models import ConfiguracaoDisparo, Contato
from .telefones import chave, normalizar


def texto(valor):
    if valor is None:
        return ''
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def cabecalho(valor):
    return unicodedata.normalize('NFKD', texto(valor)).encode('ascii', 'ignore').decode().lower()


def ler(arquivo):
    if arquivo.size > 10 * 1024 * 1024:
        raise ValueError('A planilha deve ter no máximo 10 MB.')
    nome = arquivo.name.lower()
    if nome.endswith('.xlsx'):
        from openpyxl import load_workbook
        try:
            with ZipFile(arquivo) as z:
                if sum(i.file_size for i in z.infolist()) > 50 * 1024 * 1024:
                    raise ValueError('A planilha descompactada é grande demais.')
            arquivo.seek(0)
            wb = load_workbook(arquivo, read_only=True, data_only=False)
            try:
                linhas = list(islice(wb.active.iter_rows(values_only=True), 20022))
            finally:
                wb.close()
        except (BadZipFile, KeyError, OSError) as e:
            raise ValueError('Não foi possível ler o arquivo Excel.') from e
    elif nome.endswith('.csv'):
        conteudo = arquivo.read().decode('utf-8-sig')
        try:
            dialeto = csv.Sniffer().sniff(conteudo[:4096], delimiters=',;\t')
        except csv.Error:
            dialeto = csv.excel
        linhas = list(islice(csv.reader(io.StringIO(conteudo), dialeto), 20022))
    else:
        raise ValueError('Envie um arquivo .xlsx ou .csv.')
    if len(linhas) >= 20022:
        raise ValueError('Arquivo com linhas demais. Importe até 20.000 pacientes por vez.')
    for indice, linha in enumerate(linhas[:20]):
        nomes = [cabecalho(v) for v in linha]
        if 'nome' in nomes and ('celular' in nomes or 'telefone' in nomes):
            break
    else:
        raise ValueError('Não encontrei as colunas Nome e Celular (ou Telefone).')
    dados = []
    for linha in linhas[indice + 1:]:
        item = dict(zip(nomes, map(texto, linha)))
        if not any(item.values()):
            continue
        if any(v.startswith('=') for v in item.values()):
            raise ValueError('Use valores na planilha, sem fórmulas nas linhas de pacientes.')
        dados.append(item)
    if len(dados) > 20000:
        raise ValueError('Importe até 20.000 pacientes por arquivo.')
    return dados


def importar(arquivo, unidade):
    dados = ler(arquivo)
    novos = atualizados = revisao = 0
    ConfiguracaoDisparo.obter()
    with transaction.atomic():
        # Mesmo bloqueio do agendador: importação nunca restaura a elegibilidade.
        ConfiguracaoDisparo.objects.select_for_update().get(pk=1)
        for item in dados:
            original = item.get('celular') or item.get('telefone', '')
            numero = normalizar(original)
            nome = item.get('nome', '')
            identidade = item.get('id interno') or item.get('id')
            if not identidade:
                if not numero:
                    raise ValueError('Cada linha sem telefone válido precisa de um ID interno.')
                identidade = 'tel:' + chave(numero)
            if len(identidade) > 100 or len(nome) > 250 or len(original) > 100:
                raise ValueError('ID, nome ou telefone ultrapassa o tamanho permitido.')
            motivo = 'Nome ausente.' if not nome else 'Telefone brasileiro inválido; revisar.' if not numero else ''
            contato, criado = Contato.objects.get_or_create(unidade=unidade, id_origem=identidade, defaults={
                'nome': nome, 'telefone_original': original,
            })
            # Dados do cadastro podem mudar; snapshots e reservas permanecem imutáveis.
            contato.nome = nome
            if criado or contato.telefone == normalizar(contato.telefone_original):
                contato.telefone = numero
                contato.chave_numero = chave(numero)
            contato.grupo = item.get('grupo', '')[:150]
            contato.prontuario = item.get('prontuario', '')[:100]
            contato.situacao_origem = item.get('situacao', '')[:100]
            contato.revisao = 'Nome ausente.' if not nome else 'Telefone brasileiro inválido; revisar.' if not contato.telefone else ''
            contato.save()
            novos += int(criado)
            atualizados += int(not criado)
            revisao += int(bool(motivo))
    return novos, atualizados, revisao
