from __future__ import annotations
from pathlib import Path
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.genai import types as genai_types
from document_processor.models.sync_gemini import SyncGemini
from logger import get_logger
from logger.adk_callbacks import make_before_tool_callback, make_after_tool_callback

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)


# Ajuste os paths conforme seu projeto
BASE_DIR = Path(__file__).resolve().parent
PROMPT_PATH = BASE_DIR / "prompt-gerador-markdown-tobe.txt"

def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


PROMPT_BASE = _read_text(PROMPT_PATH)

# Instrução para gerar o markdown do documento de processo
ENHANCED_INSTRUCTION = """
<VALIDAÇÃO_INTERNA>
Execute estas verificações INTERNAMENTE (não inclua este processo no documento final):

CHECKLIST PRÉ-PROCESSAMENTO:
- Verificar se {pdf_input_json?} existe no contexto. Caso contrário → retornar erro.
- Extrair bloco JSON do contexto (ignorar texto antes de '{' e após '}').
- Validar presença dos campos obrigatórios: schemaVersion, meta, columns, rows.
- Se validação falhar → retornar mensagem de erro descritiva.
- Se validação passar → prosseguir para geração do documento.

IMPORTANTE: Esta seção é APENAS para orientar seu processamento interno. 
NÃO inclua perguntas, respostas de validação ou este checklist no documento Markdown final.
</VALIDAÇÃO_INTERNA>

""" + PROMPT_BASE + """

<FORMATO_RESPOSTA_OBRIGATÓRIO>
=============================================================================
ATENÇÃO: Sua resposta completa deve ser EXCLUSIVAMENTE o documento Markdown.
=============================================================================

ESTRUTURA ESPERADA DA SUA RESPOSTA:
- Inicia com: # [Título do Documento]
- Contém: Todas as seções
- Termina com: última linha do markdown

PROIBIDO NA RESPOSTA:
- Texto antes do markdown (ex: "Aqui está o documento...")
- Texto depois do markdown (ex: "Documento gerado com sucesso")
- Code fences (```markdown ... ```)
- Respostas às validações de entrada
- Mensagens de status ou progresso
- Explicações sobre o processo de geração

A conversão Markdown → PDF será feita automaticamente por outra ferramenta.
Você é responsável APENAS pelo conteúdo Markdown puro.
</FORMATO_RESPOSTA_OBRIGATÓRIO>
"""

# ── Logging ──────────────────────────────────────────────────────
_logger = get_logger()
_before_tool_cb = make_before_tool_callback(_logger)
_after_tool_cb = make_after_tool_callback(_logger, _before_tool_cb)

tobe_subagent = Agent(
    name="agente_gerador_md_tobe",
    model=SyncGemini(model="gemini-2.5-flash"),
    description="Gera documentação de processo em Markdown. Retorna APENAS o conteúdo Markdown completo.",
    instruction=ENHANCED_INSTRUCTION,
    tools=[],  # sem tools — apenas gera markdown, salvo via output_key
    output_key="tobe_markdown",  # salva o markdown gerado no state
    before_tool_callback=_before_tool_cb,
    after_tool_callback=_after_tool_cb,
    generate_content_config=genai_types.GenerateContentConfig(
        # Desativado temporariamente (era 8192): as duas últimas travadas em
        # produção pararam exatamente numa chamada com thinking ativo — o
        # as_is_agent (thinking_budget=0) nunca travou. Teste de mitigação,
        # ver [[agente_processos_chat_hang_incident]] (2026-08-06).
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
        # Sem timeout explícito, uma chamada travada no backend do Gemini
        # espera para sempre (default do google-genai é timeout=None).
        http_options=genai_types.HttpOptions(
            timeout=180_000,
            retry_options=genai_types.HttpRetryOptions(attempts=2),
        ),
    ),
)