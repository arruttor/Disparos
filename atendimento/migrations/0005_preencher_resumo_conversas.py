"""Preenche o resumo da última mensagem nas conversas que já existiam antes da caixa de entrada."""

from django.db import migrations


def preencher(apps, schema_editor):
    Conversa = apps.get_model("atendimento", "Conversa")
    Mensagem = apps.get_model("atendimento", "Mensagem")
    for conversa in Conversa.objects.all():
        ultima = Mensagem.objects.filter(conversa=conversa).exclude(origem="nota").order_by("-criada_em").first()
        if ultima is None:
            continue
        Conversa.objects.filter(pk=conversa.pk).update(
            ultima_mensagem_em=ultima.criada_em,
            ultima_mensagem_origem=ultima.origem,
            ultima_mensagem_texto=ultima.texto[:140],
        )


class Migration(migrations.Migration):
    dependencies = [("atendimento", "0004_caixa_de_entrada")]

    operations = [migrations.RunPython(preencher, migrations.RunPython.noop)]
