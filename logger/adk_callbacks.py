"""
logger/adk_callbacks.py
-----------------------
Funções de callback compatíveis com o ADK (google-adk) para interceptar
chamadas de tools e sub-agentes (AgentTool).

As funções `make_before_tool_callback` e `make_after_tool_callback` retornam
closures prontas para serem passadas diretamente nos parâmetros
`before_tool_callback` e `after_tool_callback` do construtor `Agent` do ADK.

Rastreamento de tempo:
    O timestamp de início é armazenado em um dict interno da closure.
    A chave é (session_id, tool_name) para suportar execuções paralelas.

Extração do session_id:
    Tentativa em múltiplos caminhos da ToolContext para compatibilidade
    com diferentes versões do ADK.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from logger.base import LogEvent, ProcessLogger, summarize_args


def make_before_tool_callback(logger: ProcessLogger):
    """
    Retorna um `before_tool_callback` que registra a entrada de cada tool.

    O callback é compatível com a assinatura esperada pelo ADK:
        before_tool_callback(tool, args, tool_context) -> Optional[dict]

    Retorna None para que a execução normal da tool prossiga.
    """
    _start_times: dict[tuple, float] = {}
    _lock = threading.Lock()

    def before_tool_callback(tool, args: dict[str, Any], tool_context) -> None:
        tool_name = _get_tool_name(tool)
        session_id = _get_session_id(tool_context)
        key = (session_id, tool_name)

        with _lock:
            _start_times[key] = time.monotonic()

        event = LogEvent(
            event_type="tool_start",
            tool_name=tool_name,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
            args_summary=summarize_args(args),
        )
        try:
            logger.log_tool_start(event)
        except Exception:
            pass  # log nunca deve derrubar o pipeline

        return None  # permite que a tool execute normalmente

    # Expõe o dict de start_times para o after_callback compartilhar
    before_tool_callback._start_times = _start_times  # type: ignore[attr-defined]
    before_tool_callback._lock = _lock  # type: ignore[attr-defined]
    return before_tool_callback


def make_after_tool_callback(logger: ProcessLogger, before_callback=None):
    """
    Retorna um `after_tool_callback` que registra a saída de cada tool.

    Parâmetros
    ----------
    logger : ProcessLogger
        Instância do logger a ser usado.
    before_callback : callable, opcional
        O before_callback retornado por `make_before_tool_callback` —
        usado para recuperar o timestamp de início e calcular duration_ms.
        Se None, duration_ms não será calculado.

    O callback é compatível com a assinatura esperada pelo ADK:
        after_tool_callback(tool, args, tool_context, tool_response) -> Optional[dict]

    Retorna None para manter a resposta original da tool.
    """
    def after_tool_callback(tool, args: dict[str, Any], tool_context, tool_response) -> None:
        tool_name = _get_tool_name(tool)
        session_id = _get_session_id(tool_context)
        duration_ms = None

        if before_callback is not None:
            key = (session_id, tool_name)
            start_times = getattr(before_callback, "_start_times", {})
            lock = getattr(before_callback, "_lock", threading.Lock())
            with lock:
                start = start_times.pop(key, None)
            if start is not None:
                duration_ms = round((time.monotonic() - start) * 1000, 2)

        event = LogEvent(
            event_type="tool_end",
            tool_name=tool_name,
            session_id=session_id,
            timestamp=datetime.now(timezone.utc),
            duration_ms=duration_ms,
            args_summary=summarize_args(args),
            result_summary=tool_response,
        )
        try:
            logger.log_tool_end(event)
        except Exception:
            pass

        return None  # mantém a resposta original da tool

    return after_tool_callback


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def _get_tool_name(tool) -> str:
    """Extrai o nome da tool de forma defensiva."""
    return getattr(tool, "name", None) or type(tool).__name__


def _get_session_id(tool_context) -> str:
    """
    Tenta extrair o session ID do ToolContext através de múltiplos caminhos.
    Retorna "unknown" se nenhuma opção funcionar.
    """
    candidates = [
        lambda tc: tc.invocation_context.session.id,
        lambda tc: tc.invocation_context.session_id,
        lambda tc: tc.session_id,
        lambda tc: tc.invocation_context.session.session_id,
    ]
    for getter in candidates:
        try:
            val = getter(tool_context)
            if val:
                return str(val)
        except (AttributeError, TypeError):
            continue
    return "unknown"
