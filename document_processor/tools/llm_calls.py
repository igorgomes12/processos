"""
document_processor/tools/llm_calls.py
---------------------------------------
Chamadas diretas ao modelo Gemini via `google-genai`, síncronas, isoladas no
pool de threads dedicado da aplicação (`run_in_app_executor`) — sem passar
pelo `AgentTool`/Runner aninhado do ADK.

Por quê: a investigação do incidente de travamento intermitente (2026-08-06)
localizou uma versão anterior deste agente, validada em produção sem os
travamentos investigados, que usa exatamente este padrão para as etapas de
geração de conteúdo (extração AS-IS, Markdown AS-IS/TO-BE) — nunca cria uma
sessão/Runner ADK aninhada por chamada (o que o `AgentTool.run_async` faz
internamente, e que produzia repetidamente os logs "Closing runner... Runner
closed." observados nos incidentes). Ver `agente_processos_chat_hang_incident`
na memória do projeto para o histórico completo.
"""

from __future__ import annotations

import functools
import logging
import time
from typing import Any

from google import genai
from google.genai import types as genai_types

from document_processor.tools.executor import run_in_app_executor

logger = logging.getLogger(__name__)

_LLM_MAX_RETRIES = 3
_LLM_BASE_DELAY = 10  # segundos, dobra a cada tentativa (backoff exponencial)


def _response_text(response: Any) -> str:
    text = getattr(response, "text", None)
    if text and str(text).strip():
        return str(text)

    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if not content:
            continue
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text and str(part_text).strip():
                return str(part_text)

    return ""


def _build_client() -> genai.Client:
    return genai.Client()


def _call_llm_sync(
    *,
    model: str,
    system_instruction: str,
    user_prompt: str,
    thinking_budget: int,
) -> str:
    client = _build_client()
    config = genai_types.GenerateContentConfig(
        system_instruction=system_instruction,
        thinking_config=genai_types.ThinkingConfig(thinking_budget=thinking_budget),
    )
    last_exc: Exception | None = None
    for attempt in range(_LLM_MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            )
            return _response_text(response).strip()
        except Exception as exc:
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                last_exc = exc
                if attempt < _LLM_MAX_RETRIES:
                    delay = _LLM_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "[LLM] 429 RESOURCE_EXHAUSTED (tentativa %d/%d). "
                        "Aguardando %ds antes de retry...",
                        attempt + 1, _LLM_MAX_RETRIES + 1, delay,
                    )
                    time.sleep(delay)
                    continue
            raise
    raise last_exc  # type: ignore[misc]


async def call_llm(
    *,
    model: str,
    system_instruction: str,
    user_prompt: str,
    thinking_budget: int,
) -> str:
    """Chama o Gemini de forma síncrona, isolada no pool dedicado da aplicação.

    Substitui o padrão anterior (`AgentTool.run_async` de um sub-agente ADK
    completo) por uma chamada direta e stateless — mais simples, sem overhead
    de sessão/Runner aninhado, e alinhada com o padrão já validado em
    produção nesta versão anterior do agente.
    """
    call = functools.partial(
        _call_llm_sync,
        model=model,
        system_instruction=system_instruction,
        user_prompt=user_prompt,
        thinking_budget=thinking_budget,
    )
    return await run_in_app_executor(call)
