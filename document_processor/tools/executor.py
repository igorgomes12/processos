"""
document_processor/tools/executor.py
-------------------------------------
Pool de threads dedicado para I/O bloqueante da aplicação (Postgres, geração
de XLSX/PDF, renderização de diagrama Mermaid via API externa, conversão de
DOCX).

Por quê: `asyncio.to_thread()` usa o `ThreadPoolExecutor` PADRÃO do processo
(`loop.run_in_executor(None, fn)`), que é o MESMO pool que o `google-genai`
usa internamente para renovar token de autenticação antes de qualquer
chamada ao Gemini (`_api_client.py`, `async_get_token_from_credentials` ->
`asyncio.to_thread(refresh_auth, credentials)`, sem timeout algum).

`asyncio.wait_for(asyncio.to_thread(...), timeout=N)` cancela a ESPERA pelo
resultado, mas não consegue matar a thread do SO por baixo se ela já estiver
bloqueada numa chamada síncrona sem timeout interno (ex: uma conexão TCP ao
Cloud SQL que trava numa rede que descarta pacotes silenciosamente). Essa
thread nunca volta pro pool — é um vazamento permanente de 1 slot.

Se isso se repetir algumas vezes no mesmo worker "quente" do Agent Engine
(processo reaproveitado entre turnos/sessões), o pool padrão pode se esgotar
por completo. A partir daí, QUALQUER outra chamada `asyncio.to_thread` no
mesmo processo — inclusive a renovação de token do `google-genai`, que não
temos como instrumentar — fica enfileirada esperando um worker livre que
nunca aparece. Resultado: travamento silencioso, sem erro, em pontos
aparentemente aleatórios (depende de quantos slots já vazaram antes).

Isolando todo I/O bloqueante NOSSO neste pool dedicado, um vazamento aqui
nunca pode sufocar o pool padrão do processo — que fica livre pro que o SDK
do Google precisa internamente.

Ver: incidentes de travamento intermitente 2026-08-05/06 (memória do
projeto / resumo técnico do chamado de suporte).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")

# max_workers moderado: o suficiente para não serializar etapas concorrentes,
# mas pequeno o bastante para não mascarar um esgotamento em teste.
_APP_IO_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="app-io",
)


async def run_in_app_executor(fn: Callable[..., _T], *args: Any) -> _T:
    """Executa `fn(*args)` (síncrona) no pool de threads dedicado da aplicação.

    Substituto direto de `asyncio.to_thread(fn, *args)` — mesma assinatura,
    mas isolado do pool padrão do processo (ver docstring do módulo).
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_APP_IO_EXECUTOR, fn, *args)
