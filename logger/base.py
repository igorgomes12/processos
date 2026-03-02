"""
logger/base.py
--------------
Interface abstrata (ABC) para todos os loggers do sistema agente-processos.
Todos os backends (local, cloud, composite) devem implementar esta interface.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ──────────────────────────────────────────────────────────────────
# Evento de log estruturado
# ──────────────────────────────────────────────────────────────────

@dataclass
class LogEvent:
    """Representa um evento único de log de chamada de tool/sub-agente."""

    # Tipo do evento
    event_type: str  # "tool_start" | "tool_end" | "tool_error"

    # Identificação
    tool_name: str
    session_id: str

    # Temporização
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float | None = None  # preenchido em tool_end / tool_error

    # Payload (resumido para evitar logar dados sensíveis ou muito grandes)
    args_summary: dict[str, Any] = field(default_factory=dict)
    result_summary: Any = None
    error_msg: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serializa o evento para um dicionário plano (JSON-serializable)."""
        return {
            "event_type": self.event_type,
            "tool_name": self.tool_name,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
            "args_summary": self.args_summary,
            "result_summary": _truncate(self.result_summary),
            "error_msg": self.error_msg,
        }


# ──────────────────────────────────────────────────────────────────
# Interface abstrata
# ──────────────────────────────────────────────────────────────────

class ProcessLogger(abc.ABC):
    """Interface que todos os backends de log devem implementar."""

    @abc.abstractmethod
    def log_tool_start(self, event: LogEvent) -> None:
        """Registra o início de uma chamada de tool ou sub-agente."""

    @abc.abstractmethod
    def log_tool_end(self, event: LogEvent) -> None:
        """Registra a conclusão bem-sucedida de uma tool (inclui resultado e duração)."""

    @abc.abstractmethod
    def log_tool_error(self, event: LogEvent) -> None:
        """Registra uma falha durante a execução de uma tool."""


# ──────────────────────────────────────────────────────────────────
# Helpers internos
# ──────────────────────────────────────────────────────────────────

_MAX_VALUE_LEN = 500  # caracteres máximos para resumo de um valor


def _truncate(value: Any, max_len: int = _MAX_VALUE_LEN) -> Any:
    """Converte value para string e trunca se necessário."""
    if value is None:
        return None
    text = str(value)
    if len(text) > max_len:
        return text[:max_len] + f"… [truncado, total={len(text)} chars]"
    return text


def summarize_args(args: dict[str, Any], max_len: int = _MAX_VALUE_LEN) -> dict[str, Any]:
    """
    Cria um resumo dos argumentos de entrada de uma tool.
    Cada valor é convertido para string e truncado individualmente.
    Útil para evitar logar conteúdos binários ou JSONs muito grandes.
    """
    return {k: _truncate(v, max_len) for k, v in (args or {}).items()}
