"""
document_processor/models/sync_gemini.py
------------------------------------------
Variante do modelo Gemini do ADK que força chamadas não-streaming a usar o
client SÍNCRONO do google-genai (`client.models.generate_content`, não
`client.aio.models.generate_content`), executado isolado no pool de threads
dedicado da aplicação (`run_in_app_executor`).

Por quê: a investigação do incidente de travamento intermitente (2026-08-06)
mostrou, por duas vezes — primeiro na escrita do Firestore via `AsyncClient`,
depois nas próprias chamadas ao modelo feitas pelo ADK — que chamadas via
transporte assíncrono nativo do Google podem travar indefinidamente no
ambiente do Vertex AI Agent Engine, de um jeito que nem `asyncio.wait_for`
consegue cancelar. Nunca reproduziu localmente. Trocar o Firestore para o
client síncrono isolado em `run_in_app_executor` resolveu aquele ponto
específico (ver `document_processor/tools/generate_artifacts.py`); esta
classe aplica o mesmo padrão, agora validado, para as chamadas ao modelo.

Streaming, a API de interactions e context caching continuam delegando pro
comportamento padrão do ADK — não são usados por este projeto hoje, e
replicar esses caminhos síncronos seria risco desnecessário.

Uso: em vez de `model="gemini-2.5-flash"`, usar
`model=SyncGemini(model="gemini-2.5-flash")` no construtor do `Agent`.
"""

from __future__ import annotations

import functools
import logging
from typing import AsyncGenerator

from google.adk.models import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from google.genai.errors import ClientError

from document_processor.tools.executor import run_in_app_executor

logger = logging.getLogger(__name__)


class SyncGemini(Gemini):
    """Gemini do ADK com chamadas não-streaming via client síncrono isolado."""

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream or self.use_interactions_api or llm_request.cache_config:
            # Streaming / interactions API / context caching: delega pro
            # comportamento padrão do ADK (não usado por este projeto hoje).
            async for llm_response in super().generate_content_async(
                llm_request, stream
            ):
                yield llm_response
            return

        await self._preprocess_request(llm_request)
        self._maybe_append_user_content(llm_request)

        # Mesmo tratamento de headers/api_version do Gemini original do ADK.
        if llm_request.config:
            if not llm_request.config.http_options:
                llm_request.config.http_options = types.HttpOptions()
            llm_request.config.http_options.headers = self._merge_tracking_headers(
                llm_request.config.http_options.headers
            )
            _, api_version = self._base_url_and_api_version
            if api_version:
                llm_request.config.http_options.api_version = api_version

        logger.info(
            "Sending out request (sync/isolado), model: %s, backend: %s",
            llm_request.model, self._api_backend,
        )

        try:
            call = functools.partial(
                self.api_client.models.generate_content,
                model=llm_request.model,
                contents=llm_request.contents,
                config=llm_request.config,
            )
            response = await run_in_app_executor(call)
            logger.info("Response received from the model (sync/isolado).")
            yield LlmResponse.create(response)
        except ClientError as ce:
            if ce.code == 429:
                from google.adk.models.google_llm import _ResourceExhaustedError
                raise _ResourceExhaustedError(ce) from ce
            raise
