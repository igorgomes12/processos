"""
logger/factory.py
-----------------
Fábrica de ProcessLogger controlada pela variável de ambiente LOG_BACKEND.

Valores aceitos para LOG_BACKEND:
  local  → LocalFileLogger  (padrão quando não definida)
  cloud  → CloudLogger
  both   → CompositeLogger([LocalFileLogger, CloudLogger])

Variáveis de ambiente adicionais (opcionais):
  LOG_DIR      — diretório para LocalFileLogger (padrão: <raiz>/logs/)
  LOG_FILE     — nome do arquivo de log (padrão: agente-processos.log)
  GCP_PROJECT_ID — projeto GCP para CloudLogger
  GCP_LOG_NAME   — nome do log no Cloud Logging
"""

from __future__ import annotations

import os

from logger.base import ProcessLogger


def create_logger() -> ProcessLogger:
    """
    Cria e retorna a instância de ProcessLogger configurada por LOG_BACKEND.

    Retorna
    -------
    ProcessLogger
        Uma instância de LocalFileLogger, CloudLogger ou CompositeLogger.
    """
    backend = os.getenv("LOG_BACKEND", "local").strip().lower()

    if backend == "cloud":
        return _make_cloud()
    elif backend == "both":
        from logger.composite_logger import CompositeLogger
        return CompositeLogger([_make_local(), _make_cloud()])
    else:
        # "local" ou qualquer valor não reconhecido → local
        return _make_local()


# ──────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────

def _make_local():
    from logger.local_logger import LocalFileLogger
    log_dir = os.getenv("LOG_DIR") or None
    log_file = os.getenv("LOG_FILE", "agente-processos.log")
    return LocalFileLogger(log_dir=log_dir, log_file=log_file)


def _make_cloud():
    from logger.cloud_logger import CloudLogger
    return CloudLogger()
