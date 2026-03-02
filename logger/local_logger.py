"""
logger/local_logger.py
----------------------
Implementação de ProcessLogger que escreve logs em arquivo local
usando o módulo padrão `logging` do Python.

Formato: JSON-lines (uma entrada JSON por linha) com rotação por tamanho.
Arquivo padrão: logs/agente-processos.log
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
from pathlib import Path

from logger.base import LogEvent, ProcessLogger


_DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_DEFAULT_LOG_FILE = "agente-processos.log"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB por arquivo
_BACKUP_COUNT = 5


class _JsonFormatter(logging.Formatter):
    """Formata cada LogRecord como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:  # type: ignore[override]
        payload: dict = record.__dict__.get("log_event", {})
        return json.dumps(payload, ensure_ascii=False)


class LocalFileLogger(ProcessLogger):
    """
    Logger que persiste eventos em um arquivo JSON-lines rotacionado.

    Parâmetros
    ----------
    log_dir : str | Path, opcional
        Diretório onde os arquivos de log serão criados.
        Padrão: <raiz_do_projeto>/logs/
    log_file : str, opcional
        Nome do arquivo de log. Padrão: agente-processos.log
    level : int, opcional
        Nível mínimo de log do Python. Padrão: logging.DEBUG
    """

    def __init__(
        self,
        log_dir: str | Path | None = None,
        log_file: str = _DEFAULT_LOG_FILE,
        level: int = logging.DEBUG,
    ) -> None:
        log_dir = Path(log_dir) if log_dir else _DEFAULT_LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = log_dir / log_file

        self._logger = logging.getLogger(f"agente_processos.{log_file}")
        self._logger.setLevel(level)
        self._logger.propagate = False  # evita duplicação no root logger

        # Adiciona handler apenas uma vez (para evitar duplicação em recargas)
        if not self._logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                filename=log_path,
                maxBytes=_MAX_BYTES,
                backupCount=_BACKUP_COUNT,
                encoding="utf-8",
            )
            handler.setFormatter(_JsonFormatter())
            self._logger.addHandler(handler)

    # ──────────────────────────────────────────────────────────────
    # Interface
    # ──────────────────────────────────────────────────────────────

    def log_tool_start(self, event: LogEvent) -> None:
        self._emit(logging.INFO, event)

    def log_tool_end(self, event: LogEvent) -> None:
        self._emit(logging.INFO, event)

    def log_tool_error(self, event: LogEvent) -> None:
        self._emit(logging.ERROR, event)

    # ──────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────

    def _emit(self, level: int, event: LogEvent) -> None:
        record = logging.LogRecord(
            name=self._logger.name,
            level=level,
            pathname="",
            lineno=0,
            msg="",
            args=(),
            exc_info=None,
        )
        record.log_event = event.to_dict()
        self._logger.handle(record)
