import asyncio
import io
import logging
import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.tools import agent_tool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types
from as_is.agent import root_agent as as_is_root_agent
from agente_gerador_pdf_md.agent import pdf_subagent as agente_gerador_pdf_md_root_agent
from agente_gerador_md_tobe.agent import tobe_subagent
from document_processor.tools.generate_artifacts import (
    save_to_firestore_from_state,
    generate_xlsx_from_state,
    generate_pdf_from_state,
    generate_pdf_tobe_from_state,
    save_mermaid_from_state,
)
from document_processor.tools.postgres_tool import save_to_postgres_from_state
from logger import get_logger
from logger.adk_callbacks import make_before_tool_callback, make_after_tool_callback

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env", override=True)

# No Vertex AI (GOOGLE_GENAI_USE_VERTEXAI=1), GOOGLE_API_KEY conflita com
# project/location e causa ValueError no inicializador do cliente genai.
# Remove a variável para garantir que apenas ADC seja usado.
if os.getenv("GOOGLE_GENAI_USE_VERTEXAI") == "1":
    os.environ.pop("GOOGLE_API_KEY", None)

model_name = os.getenv("MODEL", "gemini-2.5-flash")

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# Limite de caracteres por arquivo DOCX extraído (~100k chars ≈ ~70 páginas densas).
# Arquivos maiores são truncados para evitar context overflow e travamentos.
_DOCX_MAX_CHARS = 100_000


def _converter_docx_para_texto(dados: bytes) -> str:
    """Extrai texto de um arquivo DOCX usando python-docx (síncrono, roda em thread)."""
    from docx import Document  # import lazy para não quebrar se não instalado
    doc = Document(io.BytesIO(dados))
    partes = []
    for paragrafo in doc.paragraphs:
        if paragrafo.text.strip():
            partes.append(paragrafo.text)
    for tabela in doc.tables:
        for linha in tabela.rows:
            celulas = [c.text.strip() for c in linha.cells if c.text.strip()]
            if celulas:
                partes.append(" | ".join(celulas))
    texto = "\n".join(partes)
    if len(texto) > _DOCX_MAX_CHARS:
        logging.warning(
            "[DOCX] Texto extraído truncado de %d para %d caracteres.",
            len(texto), _DOCX_MAX_CHARS,
        )
        texto = texto[:_DOCX_MAX_CHARS] + "\n\n[... conteúdo truncado por limite de tamanho ...]"
    return texto


async def before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> Optional[genai_types.GenerateContentResponse]:
    """Converte automaticamente arquivos DOCX em texto antes de enviar ao Gemini.

    O Gemini não suporta o MIME type DOCX nativamente. Este callback intercepta
    qualquer Part com inline_data de DOCX e substitui pelo texto extraído.
    A extração é executada em asyncio.to_thread para não bloquear o event loop.
    """
    for content in llm_request.contents:
        if not content.parts:
            continue
        novos_parts = []
        for part in content.parts:
            if (
                part.inline_data
                and part.inline_data.mime_type == _DOCX_MIME
                and part.inline_data.data
            ):
                try:
                    dados = bytes(part.inline_data.data)
                    texto = await asyncio.to_thread(_converter_docx_para_texto, dados)
                    logging.info("[DOCX] Arquivo convertido para texto (%d chars).", len(texto))
                    novos_parts.append(genai_types.Part(text=f"[Conteúdo extraído do DOCX]\n\n{texto}"))
                except Exception as exc:
                    logging.error("[DOCX] Falha na conversão: %s", exc)
                    novos_parts.append(genai_types.Part(
                        text="[Erro ao extrair conteúdo do DOCX — arquivo pode estar corrompido ou protegido.]"
                    ))
            else:
                novos_parts.append(part)
        content.parts = novos_parts
    return None  # continua o fluxo normal


as_is_agent = agent_tool.AgentTool(agent=as_is_root_agent)
pdf_subagent = agent_tool.AgentTool(agent=agente_gerador_pdf_md_root_agent)
tobe_subagent_tool = agent_tool.AgentTool(agent=tobe_subagent)

def preparar_state_inicial(tool_context: ToolContext) -> dict:
    """
    Executado uma única vez no início do turno.
    Prepara dados que TODOS os subagents podem consumir.
    """

    BASE_DIR = Path(__file__).parent.resolve()
 
    MODEL_MD_PATH = (
        BASE_DIR
        / "data-models"
        / "modelo_documento_processo_instrucoes_v3.md"
    )
 
    MARKDOWN_BASE = MODEL_MD_PATH.read_text(encoding="utf-8") 
    tool_context.state["pdf_model_md"] = MARKDOWN_BASE
    return {"status": "success"}

# ── Logging ──────────────────────────────────────────────────────
_logger = get_logger()
_before_tool_cb = make_before_tool_callback(_logger)
_after_tool_cb = make_after_tool_callback(_logger, _before_tool_cb)

root_agent = Agent(
    name = "document_processor",
    model=model_name,
    description="""Assitente especialista em processos de negócios responsável por analisar arquivos com documentos
    que representam processos de negócios e mapear o AS IS do processo.
    """,
    instruction="""
        ═══════════════════════════════════════════════════════════════════════
        PROTOCOLO DE ORQUESTRAÇÃO (TURNO ÚNICO, SEM INTERROMPER)
        ═══════════════════════════════════════════════════════════════════════

        OBJETIVO:
        Ao receber um documento de processo de negócio, executar TODO o pipeline
        de tools em sequência e responder ao usuário apenas no fim, com as
        mensagens literais de download.

        REGRA MESTRA:
        - NÃO envie resposta parcial.
        - NÃO pare após erro de tool.
        - Enquanto houver etapa pendente, chame a próxima tool obrigatória.
        - Só responda ao usuário depois de chamar `generate_pdf_tobe_from_state`.

        VALIDAÇÃO INICIAL:
        - Verifique se a entrada é legível e pertinente a processos de negócio.
        - Se não for pertinente, recuse educadamente e NÃO execute tools.

        ORDEM OBRIGATÓRIA DE EXECUÇÃO:
        1) `preparar_state_inicial`
        2) `as_is_agent` com request contendo o conteúdo completo do documento
        3) `save_to_firestore_from_state`
        4) `save_to_postgres_from_state`
        5) `pdf_subagent` com request para gerar Markdown AS-IS usando state
        6) `generate_xlsx_from_state` (salvar retorno em <MENSAGEM_XLSX>)
        7) `generate_pdf_from_state` (salvar retorno em <MENSAGEM_PDF>)
        8) `save_mermaid_from_state` (best-effort; continue mesmo com erro)
        9) `tobe_subagent_tool` com request para gerar Markdown TO-BE usando state
        10) `generate_pdf_tobe_from_state` (salvar retorno em <MENSAGEM_PDF_TOBE>)

        POLÍTICA DE CONTINUIDADE:
        - Se qualquer tool falhar, registre internamente o erro e siga para a próxima.
        - Nunca encerre o turno antes da etapa 10.
        - Não peça confirmação do usuário durante o fluxo.

        RESPOSTA FINAL (APENAS APÓS A ETAPA 10):
        Use EXATAMENTE o template abaixo, substituindo placeholders pelas mensagens
        LITERAIS das tools (sem alterar nenhum caractere).

        ---------------------------------------------------------------
        Recebi o seu documento e realizei a análise completa do processo. 📄

        Os arquivos estão prontos para download:

        📊 **Planilha Excel**
        <MENSAGEM_XLSX>

        📄 **Documento PDF AS-IS**
        <MENSAGEM_PDF>

        📄 **Documento PDF TO-BE**
        <MENSAGEM_PDF_TOBE>
        ---------------------------------------------------------------

        REGRAS CRÍTICAS DA RESPOSTA FINAL:
        - Preserve literalidade total das mensagens de `generate_xlsx_from_state`,
          `generate_pdf_from_state` e `generate_pdf_tobe_from_state`.
        - NÃO parafraseie, NÃO resuma e NÃO altere pontuação/versionamento.
        - Os links de download do ADK dependem exatamente dessas mensagens.

        DIRETRIZES DE SEGURANÇA:
        - Privacidade: não repetir dados sensíveis fora do necessário.
        - Escopo: recusar arquivos fora do domínio de processos de negócio.
        - Fidelidade: não inventar etapas inexistentes no documento.
        - Integridade: não modificar a saída lógica das ferramentas.
        ═══════════════════════════════════════════════════════════════════════
    """,
    tools=[preparar_state_inicial, as_is_agent, save_to_firestore_from_state, save_to_postgres_from_state, pdf_subagent, generate_xlsx_from_state, generate_pdf_from_state, save_mermaid_from_state, tobe_subagent_tool, generate_pdf_tobe_from_state],
    before_model_callback=before_model_callback,
    before_tool_callback=_before_tool_cb,
    after_tool_callback=_after_tool_cb,
    generate_content_config=genai_types.GenerateContentConfig(
        # Desabilita o modo "thinking" do gemini-2.5-flash, que pode causar
        # respostas vazias (0 tokens de saída) ao processar PDFs com labels
        # de confidencialidade (MSIP). Com thinking_budget=0 o modelo responde
        # diretamente, garantindo que tool calls sejam geradas.
        thinking_config=genai_types.ThinkingConfig(thinking_budget=0),
    ),
)