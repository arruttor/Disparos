import re

DDDS = set('11 12 13 14 15 16 17 18 19 21 22 24 27 28 31 32 33 34 35 37 38 41 42 43 44 45 46 47 48 49 51 53 54 55 61 62 63 64 65 66 67 68 69 71 73 74 75 77 79 81 82 83 84 85 86 87 88 89 91 92 93 94 95 96 97 98 99'.split())


def normalizar(valor):
    valor = str(valor or '').strip()
    # Não adivinhar números com extensões, múltiplos telefones ou letras.
    if re.search(r'[^0-9+().\s-]', valor):
        return ''
    numero = re.sub(r'\D', '', valor)
    if numero.startswith('00'):
        numero = numero[2:]
    if len(numero) in (10, 11):
        numero = '55' + numero
    if len(numero) not in (12, 13) or not numero.startswith('55') or numero[2:4] not in DDDS:
        return ''
    local = numero[4:]
    if len(local) == 9 and (local[0] != '9' or local[1] not in '6789'):
        return ''
    if len(local) == 8 and local[0] not in '23456789':
        return ''
    return numero


def chave(numero):
    numero = normalizar(numero)
    # Celulares BR com/sem o nono dígito compartilham a mesma reserva.
    if len(numero) == 13:
        return numero[:4] + numero[5:]
    return numero


def variacoes(numero):
    numero = normalizar(numero)
    if not numero:
        return []
    alternativa = ''
    if len(numero) == 13:
        alternativa = numero[:4] + numero[5:]
    elif numero[4] in '6789':
        alternativa = numero[:4] + '9' + numero[4:]
    return [numero] + ([alternativa] if alternativa else [])
