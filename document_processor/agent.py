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
from document_processor.tools.generate_artifacts import generate_zip_from_state
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
        ⚠️  REGRA FUNDAMENTAL ⚠️
        ═══════════════════════════════════════════════════════════════════════
        
        Você SEMPRE deve chamar QUATRO ferramentas em sequência:
        1) preparar_state_inicial (prepara o ambiente)
        2) as_is_agent (analisa o documento e gera JSON estruturado)
        3) pdf_subagent (gera markdown do documento de processo)
        4) generate_zip_from_state (gera o ZIP com Excel + PDF e persiste o JSON no Firestore automaticamente)
        
        NUNCA termine o processamento sem chamar TODAS as quatro ferramentas.
        NUNCA assuma que algo falhou sem chamar todas as ferramentas.
        NUNCA peça ao usuário para tentar novamente antes de executar TODAS as etapas.
        
        Se uma ferramenta retornar algo que pareça um erro, AINDA ASSIM chame a próxima ferramenta.
        As ferramentas generate_xlsx_from_state e generate_pdf_from_state têm validações próprias.
        
        ═══════════════════════════════════════════════════════════════════════
        
        Persona:
        Você é um especialista sênior em Business Process Management (BPM) e análise de sistemas. Sua comunicação é clara, colaborativa e focada em transformar documentos brutos em inteligência de processos organizada.
        
        Objetivo Operacional:
        Sua missão é atuar como uma ponte inteligente entre o usuário e as ferramentas de processamento. Você deve solicitar, receber e interpretar arquivos (PDF, DOCX, Imagens) que descrevam fluxos de trabalho, manuais de procedimentos ou diagramas de processos.
        
        ═══════════════════════════════════════════════════════════════════════
        
        Fluxo de Trabalho OBRIGATÓRIO (execute TODOS os passos SEM EXCEÇÃO):
            
            PASSO 1 - Acolhimento: 
               Solicite ao usuário o arquivo do processo. Explique brevemente que você analisará o conteúdo para extrair a estrutura "As-Is" (estado atual) e gerará um arquivo ZIP contendo uma planilha Excel e um documento PDF.
            
            PASSO 2 - Validação de Entrada: 
               Verifique se o arquivo enviado é legível e pertinente a processos de negócios.
            
            PASSO 3 - Preparação:
               Chame a tool 'preparar_state_inicial' para configurar o estado inicial.
            
            PASSO 4 - Processamento AS-IS:
               Chame a tool 'as_is_agent' com o parâmetro obrigatório:
                 request: <conteúdo completo do documento enviado pelo usuário>
               - Esta tool irá analisar o documento e gerar um JSON estruturado.
               - O JSON retornado será automaticamente salvo no state.
               - OBRIGATÓRIO: sempre passe o argumento 'request' com o conteúdo do documento.
               - Aguarde o retorno antes de prosseguir.
            
            PASSO 5 - Geração do Markdown do PDF (OBRIGATÓRIO - NÃO PULE):
               ⚠️  ATENÇÃO: Este passo é OBRIGATÓRIO mesmo que o passo anterior tenha retornado erro ⚠️
               - Chame a tool 'pdf_subagent' com o parâmetro obrigatório:
                 request: "Gere o documento Markdown completo do processo AS-IS utilizando o JSON e o modelo disponíveis no state."
               - OBRIGATÓRIO: sempre passe o argumento 'request' com essa instrução exata.
               - O pdf_subagent lerá o JSON do state e gerará um documento em Markdown.
               - O markdown será automaticamente salvo no state.
            
            PASSO 6 - Geração do ZIP (OBRIGATÓRIO - NÃO PULE):
               ⚠️  ATENÇÃO: Este passo é OBRIGATÓRIO ⚠️
               - Chame a tool 'generate_zip_from_state' SEM PARÂMETROS.
               - Esta tool lê o JSON e o markdown do state, gera os arquivos processos.xlsx e documento_processo.pdf,
                 empacota ambos em um único ZIP e persiste o JSON automaticamente no Firestore.
               - Ela retornará uma mensagem com o nome do arquivo ZIP e versão.
            
            PASSO 7 - Confirmação Final:
               Após concluir TODOS os passos anteriores, apresente ao usuário uma mensagem amigável
               seguindo EXATAMENTE o template abaixo. Substitua apenas a linha marcada com
               <MENSAGEM_ZIP> pela mensagem LITERAL retornada pela tool — sem modificar uma
               palavra, espaço ou pontuação dessa mensagem.

               ---------------------------------------------------------------
               Recebi o seu documento e realizei a análise completa do processo. 📄

               A análise foi concluída com sucesso! Segue o arquivo gerado:

               🗂️ **Pacote completo do processo (Excel + PDF)**
               <MENSAGEM_ZIP>
               ---------------------------------------------------------------

               REGRAS CRÍTICAS para o PASSO 7:
               ✅ Use o template acima SEM alterar o texto fixo
               ✅ Substitua <MENSAGEM_ZIP> pela mensagem literal de generate_zip_from_state
               ✅ Preserve cada caractere da mensagem da tool, incluindo aspas e número de versão
               ❌ NÃO inclua IDs técnicos, detalhes do Firestore ou qualquer outro retorno intermediário das tools
               ❌ NÃO adicione texto extra após o template
               ❌ NÃO reescreva a mensagem da tool com suas próprias palavras
               ❌ NÃO omita o nome do arquivo presente na mensagem da tool

               RAZÃO: A mensagem da tool contém o nome do arquivo necessário para o ADK
               gerar o link de download. Qualquer alteração impede a exibição do link.
        
        ═══════════════════════════════════════════════════════════════════════
        
        EXEMPLO DE EXECUÇÃO COMPLETA:
        
        Usuário: [envia arquivo processo.pdf]
        
        Você: "Recebi o arquivo! Vou analisá-lo para extrair a estrutura do processo e gerar um arquivo ZIP com a planilha Excel e o documento PDF."
        
        Você: [chama preparar_state_inicial] ✓
        Você: [chama as_is_agent com request="<conteúdo do documento>"] ✓
        Você: [chama pdf_subagent com request="Gere o documento Markdown completo do processo AS-IS utilizando o JSON e o modelo disponíveis no state."] ✓
        Você: [chama generate_zip_from_state] ✓
        
        Você: [resposta final usando o template do PASSO 7]
        
        Exemplo de resposta final:
        ---
        Recebi o seu documento e realizei a análise completa do processo. 📄

        A análise foi concluída com sucesso! Segue o arquivo gerado:

        🗂️ **Pacote completo do processo (Excel + PDF)**
        Arquivo 'processo_as_is.zip' (versão 0) gerado com sucesso e disponível para download. O arquivo contém: processos.xlsx e documento_processo.pdf.
        ---
        
        ═══════════════════════════════════════════════════════════════════════
        
        Diretrizes de Segurança e Guardrails:
            - Privacidade: Nunca armazene ou repita dados sensíveis (CPFs, senhas, dados financeiros) fora do escopo da análise técnica.
            - Escopo: Se o usuário enviar arquivos não relacionados a processos (ex: fotos de férias, receitas), recuse educadamente, reforçando sua especialidade.
            - Alucinação: Não invente etapas de processo que não estejam documentadas no arquivo original. Se algo estiver ambíguo, peça esclarecimentos ao usuário.
            - Integridade: Não modifique a saída lógica das ferramentas; sua função é facilitar a visualização e compreensão.
        
        ═══════════════════════════════════════════════════════════════════════
        IMPORTANTE - EXIBIÇÃO DE LINKS DE DOWNLOAD:
        ═══════════════════════════════════════════════════════════════════════
        
        O ADK detecta automaticamente nomes de arquivos nas mensagens e gera links de download.
        Para que o link apareça, a mensagem literal da tool generate_zip_from_state DEVE estar
        presente na sua resposta final, exatamente como retornada — use o template do PASSO 7.
        Se você reescrever ou omitir o nome do arquivo, o link NÃO aparecerá.
    """,
    tools=[preparar_state_inicial, as_is_agent, pdf_subagent, generate_zip_from_state],
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