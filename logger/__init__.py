"""
logger/__init__.py
------------------
Ponto de entrada público do pacote logger.

Expõe:
  - get_logger()  → singleton de ProcessLogger configurado por LOG_BACKEND
  - ProcessLogger → interface base (para type hints)
  - LogEvent      → dataclass de evento de log

O singleton é criado na primeira chamada a get_logger() e reutilizado
em todo o processo, garantindo um único backend ativo por instância.
"""

from __future__ import annotations

from logger.base import LogEvent, ProcessLogger

_instance: ProcessLogger | None = None


def get_logger() -> ProcessLogger:
    """
    Retorna o singleton de ProcessLogger.

    Na primeira chamada cria a instância usando `logger.factory.create_logger()`
    (que lê a variável de ambiente LOG_BACKEND). Chamadas subsequentes retornam
    sempre a mesma instância.
    """
    global _instance
    if _instance is None:
        from logger.factory import create_logger
        _instance = create_logger()
    return _instance


__all__ = ["get_logger", "ProcessLogger", "LogEvent"]
