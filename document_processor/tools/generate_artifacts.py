"""
Tools determinísticas para geração de artefatos (XLSX e PDF) a partir do state,
e para persistência no Firestore.

Estas tools são chamadas pelo document_processor APÓS os sub-agentes concluírem.
Elas lêem os dados do state e garantem que os artefatos sejam criados e persistidos,
independentemente de os sub-agentes terem chamado suas próprias tools internas.
"""

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Dict, Any

from google.cloud import firestore
from google.oauth2 import service_account
from google.genai.types import Part, Blob
from google.adk.tools.tool_context import ToolContext

from agent_tools.json_to_xlsx import json_to_xlsx
from agente_gerador_pdf_md.tools.markdown_to_pdf_tool import _build_pdf
from document_processor.tools.spanner_tool import _upsert_mermaid_sync, _SPANNER_TIMEOUT

# ─── Configurações Firestore ─────────────────────────────────────────────────
_CREDENTIALS_PATH = Path(__file__).resolve().parents[2] / "credentials.json"

# Namespace UUID fixo para geração determinística de IDs (mesmo padrão do Spanner).
# Garante idempotência: reprocessar o mesmo documento sobrescreve os dados sem duplicar.
_UUID_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
_GCP_PROJECT = "steady-computer-487217-p6"
_FIRESTORE_DATABASE = "as-is-processes"
_COLLECTION_NAME = "processos"
_FIRESTORE_TIMEOUT = 30

# ─── Arquivo de debug (sobrescrito a cada execução) ─────────────────────────
_DEBUG_JSON_PATH = Path(__file__).resolve().parents[2] / "debug_last_json.json"

# ─── Singleton async + lock de thread-safety ────────────────────────────────
_firestore_async_client: firestore.AsyncClient | None = None
_firestore_client_lock = threading.Lock()


def _get_firestore_async_client() -> firestore.AsyncClient:
    """Singleton thread-safe do cliente Firestore assíncrono.

    Prioridade de autenticação:
      1. credentials.json local (desenvolvimento)
      2. Application Default Credentials (Vertex AI / Cloud Run)

    Usa threading.Lock para garantir que apenas uma instância seja criada
    mesmo em ambientes com múltiplos threads concorrentes (Vertex AI Agent Engine).
    """
    global _firestore_async_client
    with _firestore_client_lock:
        if _firestore_async_client is None:
            if _CREDENTIALS_PATH.exists():
                credentials = service_account.Credentials.from_service_account_file(
                    str(_CREDENTIALS_PATH),
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
                _firestore_async_client = firestore.AsyncClient(
                    project=_GCP_PROJECT,
                    credentials=credentials,
                    database=_FIRESTORE_DATABASE,
                )
            else:
                # Ambiente cloud (Vertex AI Agent Engine / Cloud Run):
                # usa Application Default Credentials injetadas automaticamente.
                # A service account do Reasoning Engine precisa ter
                # roles/datastore.user no projeto para que a escrita funcione.
                _firestore_async_client = firestore.AsyncClient(
                    project=_GCP_PROJECT,
                    database=_FIRESTORE_DATABASE,
                )
    return _firestore_async_client


def _make_firestore_id(data: dict) -> str:
    """Gera um ID determinístico para o documento Firestore baseado no conteúdo
    das rows (ignora meta.createdAt que muda a cada execução).
    Mesmo conteúdo → mesmo ID → upsert idempotente sem duplicatas.
    """
    chave = json.dumps(data.get("rows", data), sort_keys=True, ensure_ascii=False)
    return str(uuid.uuid5(_UUID_NAMESPACE, chave))


async def _write_to_firestore_async(data: dict) -> str:
    """Executa a escrita assíncrona no Firestore via AsyncClient nativo.

    Persiste o JSON completo como UM único documento com ID determinístico
    (UUID v5 baseado nas rows), garantindo que reprocessamentos do mesmo
    documento sobrescrevam os dados sem criar duplicatas (upsert idempotente).

    Retorna o ID do documento persistido.
    """
    client = _get_firestore_async_client()
    doc_id = _make_firestore_id(data)
    await client.collection(_COLLECTION_NAME).document(doc_id).set(data)
    return doc_id


async def save_to_firestore_from_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Lê o JSON do state (pdf_input_json) e persiste no Google Cloud Firestore.

    Esta tool é determinística: não depende do LLM para ser chamada.
    Deve ser invocada pelo document_processor logo após o as_is_agent concluir,
    garantindo a persistência independentemente do comportamento do sub-agente.

    Returns:
        Dicionário com status da operação e o ID do documento criado.
    """
    raw_json = tool_context.state.get("pdf_input_json", "")

    if not raw_json or not raw_json.strip():
        return {
            "status": "error",
            "message": "State 'pdf_input_json' está vazio. O as_is_agent não gerou o JSON.",
        }

    json_str = _extract_json_from_text(raw_json)
    if not json_str:
        return {
            "status": "error",
            "message": (
                f"Não foi possível extrair JSON válido do state para salvar no Firestore. "
                f"Conteúdo: {raw_json[:200]}"
            ),
        }

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON inválido ao salvar no Firestore: {e}"}

    try:
        doc_id = await asyncio.wait_for(
            _write_to_firestore_async(data),
            timeout=_FIRESTORE_TIMEOUT,
        )
        return {
            "status": "success",
            "message": (
                f"JSON persistido com sucesso no Firestore "
                f"(banco: '{_FIRESTORE_DATABASE}', coleção: '{_COLLECTION_NAME}', "
                f"documento: '{doc_id}'). "
                f"ID determinístico — reprocessamentos sobrescrevem sem duplicar."
            ),
        }
    except asyncio.TimeoutError:
        msg = (
            f"Timeout ao salvar no Firestore após {_FIRESTORE_TIMEOUT}s. "
            "Verifique a conectividade e as permissões da service account "
            "(roles/datastore.user necessário no Vertex AI Reasoning Engine)."
        )
        print(f"[Firestore][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}
    except Exception as e:
        msg = f"Erro ao salvar no Firestore: {e}"
        print(f"[Firestore][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

# ─── Timeouts ───────────────────────────────────────────────────────────────
_XLSX_TIMEOUT = 60
_PDF_TIMEOUT = 120


def _extract_json_from_text(text: str) -> str | None:
    """
    Extrai o bloco JSON de um texto que pode conter prefixo/sufixo.
    Retorna a string JSON ou None se não encontrar.
    """
    if not text or not text.strip():
        return None

    # Encontra o primeiro '{' e o último '}'
    first_brace = text.find("{")
    last_brace = text.rfind("}")

    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        return None

    candidate = text[first_brace : last_brace + 1]

    # Valida se é JSON válido
    try:
        parsed = json.loads(candidate)
        # Verifica campos obrigatórios
        if isinstance(parsed, dict) and "columns" in parsed and "rows" in parsed:
            return candidate
    except json.JSONDecodeError:
        pass

    return None


def _build_xlsx_bytes(json_str: str) -> bytes:
    """Gera bytes do XLSX a partir de JSON string (síncrono, roda em thread)."""
    tmp_dir = tempfile.mkdtemp()
    tmp_json = os.path.join(tmp_dir, "processos.json")
    tmp_xlsx = os.path.join(tmp_dir, "processos.xlsx")
    try:
        with open(tmp_json, "w", encoding="utf-8") as f:
            f.write(json_str)
        json_to_xlsx(tmp_json, tmp_xlsx)
        with open(tmp_xlsx, "rb") as f:
            return f.read()
    finally:
        for path in (tmp_json, tmp_xlsx):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


async def generate_xlsx_from_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Lê o JSON do state (pdf_input_json), persiste no Firestore e gera o artefato processos.xlsx.

    Esta tool é determinística: não depende do LLM.
    A persistência no Firestore é feita aqui como side-effect garantido, eliminando a
    necessidade de uma tool separada que dependeria de chamada explícita pelo LLM.

    Returns:
        Dicionário com status da operação.
    """
    raw_json = tool_context.state.get("pdf_input_json", "")

    if not raw_json or not raw_json.strip():
        return {
            "status": "error",
            "message": "State 'pdf_input_json' está vazio. O as_is_agent não gerou o JSON.",
        }

    # Extrai JSON do texto (pode ter prefixo com mensagem da tool)
    json_str = _extract_json_from_text(raw_json)
    if not json_str:
        return {
            "status": "error",
            "message": f"Não foi possível extrair JSON válido do state. Conteúdo: {raw_json[:200]}",
        }

    # ── Grava arquivo de debug (sobrescreve a cada execução) ─────────────────────
    try:
        parsed_debug = json.loads(json_str)
        with open(_DEBUG_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(parsed_debug, f, ensure_ascii=False, indent=2)
    except Exception as _e:
        print(f"[WARN] Não foi possível gravar {_DEBUG_JSON_PATH}: {_e}", file=sys.stderr)

    # ── Persistência no Firestore (side-effect garantido, independente de LLM) ──
    firestore_status = "não realizada"
    try:
        data = json.loads(json_str)
        doc_id = await asyncio.wait_for(
            _write_to_firestore_async(data),
            timeout=_FIRESTORE_TIMEOUT,
        )
        firestore_status = f"sucesso (doc: {doc_id})"
    except asyncio.TimeoutError:
        firestore_status = f"timeout após {_FIRESTORE_TIMEOUT}s"
        print(
            f"[Firestore][ERROR] Timeout ao salvar no Firestore após {_FIRESTORE_TIMEOUT}s. "
            "Verifique as permissões da service account do Vertex AI Reasoning Engine "
            "(roles/datastore.user é necessário no projeto GCP).",
            file=sys.stderr,
        )
    except Exception as e:
        firestore_status = f"erro: {e}"
        print(f"[Firestore][ERROR] Falha ao salvar no Firestore: {e}", file=sys.stderr)

    # ── Geração do XLSX ──────────────────────────────────────────────────────────
    try:
        xlsx_bytes = await asyncio.wait_for(
            asyncio.to_thread(_build_xlsx_bytes, json_str),
            timeout=_XLSX_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "message": f"Timeout ao gerar XLSX após {_XLSX_TIMEOUT}s. Firestore: {firestore_status}."}
    except Exception as e:
        return {"status": "error", "message": f"Erro ao gerar XLSX: {e}. Firestore: {firestore_status}."}

    artifact_part = Part(
        inline_data=Blob(
            data=xlsx_bytes,
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    )

    version = await tool_context.save_artifact(
        filename="processos.xlsx",
        artifact=artifact_part,
    )

    return {
        "status": "success",
        "message": (
            f"Arquivo 'processos.xlsx' (versão {version}) gerado com sucesso "
            f"e disponível para download. Firestore: {firestore_status}."
        ),
    }


_MERMAID_TIMEOUT = 60


def _extract_mermaid_block(markdown: str) -> str | None:
    """Extrai o conteúdo do primeiro bloco ```mermaid...``` presente no Markdown.

    Retorna apenas o conteúdo interno (sem as code fences), ou None se não encontrar.
    """
    match = re.search(r'```mermaid\s*\n(.*?)\n```', markdown, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


async def save_mermaid_from_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Extrai o script Mermaid do state (pdf_markdown), identifica os N0s distintos
    do state (pdf_input_json) e persiste no Spanner via upsert idempotente.

    Tabelas afetadas: N0_Mermaid + Edge_Has_Mermaid.

    Esta tool é determinística: não depende do LLM para ser chamada.
    Deve ser invocada pelo document_processor após generate_pdf_from_state.

    Returns:
        Dicionário com status da operação.
    """
    # 1. Extrai bloco Mermaid do Markdown gerado pelo pdf_subagent
    markdown = tool_context.state.get("pdf_markdown", "")
    if not markdown or not markdown.strip():
        return {
            "status": "error",
            "message": "State 'pdf_markdown' está vazio. O pdf_subagent não gerou o Markdown.",
        }

    mermaid_script = _extract_mermaid_block(markdown)
    if not mermaid_script:
        return {
            "status": "error",
            "message": "Nenhum bloco ```mermaid encontrado no pdf_markdown.",
        }

    # 2. Extrai N0s distintos do JSON gerado pelo as_is_agent
    raw_json = tool_context.state.get("pdf_input_json", "")
    json_str = _extract_json_from_text(raw_json)
    if not json_str:
        return {
            "status": "error",
            "message": "State 'pdf_input_json' inválido — não foi possível extrair N0s.",
        }

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON inválido ao extrair N0s: {e}"}

    n0_nomes = sorted({
        (r.get("N0") or "").strip()
        for r in data.get("rows", [])
        if (r.get("N0") or "").strip()
    })

    if not n0_nomes:
        return {
            "status": "error",
            "message": "Nenhum N0 encontrado no JSON para persistir o Mermaid.",
        }

    # 3. Persiste no Spanner (síncrono via thread para não bloquear o event loop)
    try:
        mensagem = await asyncio.wait_for(
            asyncio.to_thread(_upsert_mermaid_sync, n0_nomes, mermaid_script),
            timeout=_MERMAID_TIMEOUT,
        )
        return {"status": "success", "message": mensagem}

    except asyncio.TimeoutError:
        msg = (
            f"Timeout ao persistir Mermaid no Spanner após {_MERMAID_TIMEOUT}s. "
            "Verifique a conectividade e a permissão da service account."
        )
        print(f"[Spanner][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    except Exception as e:
        msg = f"Erro ao persistir Mermaid no Spanner: {e}"
        print(f"[Spanner][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}


async def generate_pdf_from_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Lê o markdown do state (pdf_markdown) e gera o artefato documento_processo.pdf.
    Esta tool é determinística: não depende do LLM, apenas lê o state e gera o arquivo.
    Deve ser chamada APÓS o agente_gerador_pdf_md concluir.

    Returns:
        Dicionário com status da operação.
    """
    markdown_content = tool_context.state.get("pdf_markdown", "")

    if not markdown_content or not markdown_content.strip():
        return {
            "status": "error",
            "message": "State 'pdf_markdown' está vazio. O agente_gerador_pdf_md não gerou o markdown.",
        }

    # Remove code fences se presentes (```json ... ``` ou ```markdown ... ```)
    cleaned = markdown_content.strip()
    if cleaned.startswith("```"):
        # Remove a primeira linha de code fence e a última
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    try:
        pdf_bytes = await asyncio.wait_for(
            asyncio.to_thread(_build_pdf, cleaned),
            timeout=_PDF_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "message": f"Timeout ao gerar PDF após {_PDF_TIMEOUT}s."}
    except Exception as e:
        return {"status": "error", "message": f"Erro ao gerar PDF: {str(e)[:200]}"}

    if not pdf_bytes or len(pdf_bytes) == 0:
        return {"status": "error", "message": "PDF gerado está vazio."}

    artifact_part = Part(
        inline_data=Blob(
            data=pdf_bytes,
            mime_type="application/pdf",
        )
    )

    version = await tool_context.save_artifact(
        filename="documento_processo.pdf",
        artifact=artifact_part,
    )

    return {
        "status": "success",
        "message": (
            f"Arquivo 'documento_processo.pdf' (versão {version}) gerado com sucesso "
            "e disponível para download."
        ),
    }

