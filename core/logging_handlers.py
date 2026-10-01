"""Handler de logging que grava os registros no banco (tela de Logs do painel)."""

import logging
import traceback


class BancoDeDadosHandler(logging.Handler):
    def emit(self, record):
        # Import tardio: o handler é criado antes dos apps estarem prontos.
        try:
            from core.models import RegistroLog

            detalhe = ""
            if record.exc_info:
                detalhe = "".join(traceback.format_exception(*record.exc_info))

            RegistroLog.objects.create(
                nivel=record.levelname,
                origem=getattr(record, "origem", "") or record.name,
                mensagem=record.getMessage()[:2000],
                detalhe=detalhe,
            )
        except Exception:
            # Nunca deixar uma falha de log derrubar a aplicação
            # (ex.: tabela ainda não migrada).
            pass
