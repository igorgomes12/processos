"""
logger/composite_logger.py
--------------------------
Implementação de ProcessLogger que delega para múltiplos backends simultaneamente.

Uso típico (LOG_BACKEND=both):
    CompositeLogger([LocalFileLogger(), CloudLogger()])

Isolamento de falhas:
    Se um logger individual lançar exceção, o erro é capturado e impresso
    no stderr sem interromper os demais loggers da lista.
"""

from __future__ import annotations

import sys
from typing import Sequence

from logger.base import LogEvent, ProcessLogger


class CompositeLogger(ProcessLogger):
    """
    Encaminha cada evento de log para todos os backends configurados.

    Parâmetros
    ----------
    loggers : Sequence[ProcessLogger]
        Lista de backends que receberão os eventos.
    """

    def __init__(self, loggers: Sequence[ProcessLogger]) -> None:
        self._loggers: list[ProcessLogger] = list(loggers)

    # ──────────────────────────────────────────────────────────────
    # Interface
    # ──────────────────────────────────────────────────────────────

    def log_tool_start(self, event: LogEvent) -> None:
        self._dispatch("log_tool_start", event)

    def log_tool_end(self, event: LogEvent) -> None:
        self._dispatch("log_tool_end", event)

    def log_tool_error(self, event: LogEvent) -> None:
        self._dispatch("log_tool_error", event)

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _dispatch(self, method_name: str, event: LogEvent) -> None:
        for logger in self._loggers:
            try:
                getattr(logger, method_name)(event)
            except Exception as exc:
                print(
                    f"[CompositeLogger] Erro em {type(logger).__name__}.{method_name}: {exc}",
                    file=sys.stderr,
                )
