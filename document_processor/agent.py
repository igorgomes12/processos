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
from document_processor.tools.generate_artifacts import (
    save_to_firestore_from_state,
    generate_xlsx_from_state,
    generate_pdf_from_state,
)
from document_processor.tools.spanner_tool import save_to_spanner_from_state
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
        FLUXO DE EXECUÇÃO — TURNO ÚNICO
        ═══════════════════════════════════════════════════════════════════════

        Quando o usuário enviar um arquivo de processo, execute os passos abaixo
        EM SEQUÊNCIA, sem pular nenhum, e sem aguardar confirmação do usuário.

        PASSO 1 - Acolhimento:
            Confirme o recebimento do arquivo e informe que irá gerar a planilha
            Excel e o documento PDF do processo.

        PASSO 2 - Validação de Entrada:
            Verifique se o arquivo é legível e pertinente a processos de negócios.
            Se não for, recuse educadamente SEM executar as ferramentas.

        PASSO 3 - Preparação:
            Chame a tool 'preparar_state_inicial' sem parâmetros.

        PASSO 4 - Processamento AS-IS:
            Chame a tool 'as_is_agent' com:
                request: <conteúdo completo do documento enviado pelo usuário>
            Aguarde o retorno antes de continuar.

        PASSO 5 - Persistência no Firestore (OBRIGATÓRIO — NÃO PULE):
            Chame a tool 'save_to_firestore_from_state' SEM PARÂMETROS.
            Aguarde o retorno antes de continuar.

        PASSO 6 - Persistência no Spanner (OBRIGATÓRIO — NÃO PULE):
            Chame a tool 'save_to_spanner_from_state' SEM PARÂMETROS.
            Aguarde o retorno antes de continuar.

        PASSO 7 - Geração do Markdown (OBRIGATÓRIO — NÃO PULE):
            Chame a tool 'pdf_subagent' com:
                request: "Gere o documento Markdown completo do processo AS-IS utilizando o JSON e o modelo disponíveis no state."
            Aguarde o retorno antes de continuar.

        PASSO 8 - Geração da planilha Excel (OBRIGATÓRIO — NÃO PULE):
            Chame a tool 'generate_xlsx_from_state' SEM PARÂMETROS.
            Guarde a mensagem retornada como <MENSAGEM_XLSX>.

        PASSO 9 - Geração do documento PDF (OBRIGATÓRIO — NÃO PULE):
            Chame a tool 'generate_pdf_from_state' SEM PARÂMETROS.
            Guarde a mensagem retornada como <MENSAGEM_PDF>.

        PASSO 10 - Apresentação do resultado:
            Responda ao usuário usando EXATAMENTE o template abaixo.
            Substitua <MENSAGEM_XLSX> pela mensagem LITERAL retornada por generate_xlsx_from_state
            e <MENSAGEM_PDF> pela mensagem LITERAL retornada por generate_pdf_from_state
            — sem modificar nenhum caractere.

            ---------------------------------------------------------------
            Recebi o seu documento e realizei a análise completa do processo. 📄

            Os arquivos estão prontos para download:

            📊 **Planilha Excel**
            <MENSAGEM_XLSX>

            📄 **Documento PDF**
            <MENSAGEM_PDF>
            ---------------------------------------------------------------

            REGRAS CRÍTICAS para o PASSO 8:
            ✅ Substitua <MENSAGEM_XLSX> pela mensagem LITERAL de generate_xlsx_from_state
            ✅ Substitua <MENSAGEM_PDF> pela mensagem LITERAL de generate_pdf_from_state
            ✅ Preserve cada caractere de cada mensagem (aspas, número de versão, pontuação)
            ❌ NÃO reescreva as mensagens das tools com suas próprias palavras
            ❌ NÃO omita o nome dos arquivos presentes nas mensagens das tools
            RAZÃO: As mensagens das tools contêm os nomes dos arquivos necessários para o ADK
            gerar os links de download. Qualquer alteração impede a exibição dos links.

        ═══════════════════════════════════════════════════════════════════════
        Diretrizes de Segurança e Guardrails:
            - Privacidade: Nunca armazene ou repita dados sensíveis (CPFs, senhas, dados financeiros).
            - Escopo: Recuse arquivos não relacionados a processos de negócios.
            - Alucinação: Não invente etapas que não estejam no documento original.
            - Integridade: Não modifique a saída lógica das ferramentas.
        ═══════════════════════════════════════════════════════════════════════
    """,
    tools=[preparar_state_inicial, as_is_agent, save_to_firestore_from_state, save_to_spanner_from_state, pdf_subagent, generate_xlsx_from_state, generate_pdf_from_state],
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