from celery import shared_task


@shared_task(name='disparos.processar_fila', ignore_result=True, max_retries=0)
def processar_fila():
    from .services import executar_rodada
    executar_rodada()
