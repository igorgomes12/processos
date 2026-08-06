"""
Tools determinísticas para geração de artefatos (XLSX e PDF) a partir do state.

Estas tools são chamadas pelo document_processor APÓS os sub-agentes concluírem.
Elas lêem os dados do state e garantem que os artefatos sejam criados e persistidos,
independentemente de os sub-agentes terem chamado suas próprias tools internas.
"""

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Any

from google.genai.types import Part, Blob
from google.adk.tools.tool_context import ToolContext

from agent_tools.json_to_xlsx import json_to_xlsx
from agente_gerador_pdf_md.tools.markdown_to_pdf_tool import _build_pdf
from document_processor.tools.postgres_tool import _upsert_mermaid_sync, _upsert_tobe_sync
from document_processor.tools.executor import run_in_app_executor

# ─── Arquivo de debug (sobrescrito a cada execução) ─────────────────────────
_DEBUG_JSON_PATH = Path(__file__).resolve().parents[2] / "debug_last_json.json"

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
    Lê o JSON do state (pdf_input_json) e gera o artefato processos.xlsx.

    Esta tool é determinística: não depende do LLM.

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

    # ── Geração do XLSX ──────────────────────────────────────────────────────────
    try:
        xlsx_bytes = await asyncio.wait_for(
            run_in_app_executor(_build_xlsx_bytes, json_str),
            timeout=_XLSX_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "message": f"Timeout ao gerar XLSX após {_XLSX_TIMEOUT}s."}
    except Exception as e:
        return {"status": "error", "message": f"Erro ao gerar XLSX: {e}."}

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
            f"e disponível para download."
        ),
    }


_MERMAID_TIMEOUT = 60
_TOBE_TIMEOUT = 60


_MERMAID_BRACKET_LABEL = re.compile(r'\[([^\[\]"]*[()][^\[\]"]*)\]')
_MERMAID_BRACE_LABEL = re.compile(r'\{([^{}"]*[()][^{}"]*)\}')


def _quote_mermaid_labels(mermaid_code: str) -> str:
    """Envolve em aspas labels de nós ([...] e {...}) que contêm parênteses.

    O parser do Mermaid trata '(' e ')' como início de outro formato de nó
    (nó arredondado/stadium); um label como A[Geração AS-IS (LLM)] sem aspas
    quebra com "Parse error... got 'PS'". Envolver em aspas
    (A["Geração AS-IS (LLM)"]) resolve sem alterar o texto do label.
    Labels que já estão entre aspas são deixados intactos (o char class
    exclui '"', então já não fazem match).
    """
    if not mermaid_code:
        return mermaid_code
    code = _MERMAID_BRACKET_LABEL.sub(lambda m: f'["{m.group(1)}"]', mermaid_code)
    code = _MERMAID_BRACE_LABEL.sub(lambda m: f'{{"{m.group(1)}"}}', code)
    return code


def _extract_mermaid_block(markdown: str) -> str | None:
    """Extrai o conteúdo do primeiro bloco ```mermaid...``` presente no Markdown.

    Retorna apenas o conteúdo interno (sem as code fences), ou None se não encontrar.

    Notas de robustez:
    - Normaliza \r\n → \n antes do match para suportar texto gerado no Windows /
      devolvido pelo LLM com quebras de linha CRLF.
    - Abre com [^\n]* (em vez de \s*) para não consumir a quebra de linha
      obrigatória após o token "mermaid".
    - Fecha com \n\s*``` para tolerar espaços antes da fence de fechamento.
    - Aplica _quote_mermaid_labels para evitar erro de parse com parênteses
      não citados em labels (ver docstring da função).
    """
    normalised = markdown.replace('\r\n', '\n').replace('\r', '\n')
    match = re.search(r'```mermaid[^\n]*\n(.*?)\n\s*```', normalised, re.DOTALL)
    if match:
        return _quote_mermaid_labels(match.group(1).strip())
    return None


async def save_mermaid_from_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Extrai o script Mermaid do state (pdf_markdown), identifica os N2s distintos
    do state (pdf_input_json) e persiste no Postgres via upsert idempotente.

    Cardinalidade: 1 script Mermaid por N2_Processo (PK garante unicidade).
    Tabelas afetadas: N2_Mermaid + Edge_Has_Mermaid.

    Esta tool é determinística: não depende do LLM para ser chamada.
    Deve ser invocada pelo document_processor após generate_pdf_from_state.

    Returns:
        Dicionário com status da operação.
    """
    # 1. Extrai bloco Mermaid do Markdown gerado pelo pdf_subagent
    markdown = tool_context.state.get("pdf_markdown", "")
    if not markdown or not markdown.strip():
        msg = "State 'pdf_markdown' está vazio. O pdf_subagent não gerou o Markdown."
        print(f"[Postgres][Mermaid][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    mermaid_script = _extract_mermaid_block(markdown)
    if not mermaid_script:
        msg = "Nenhum bloco ```mermaid encontrado no pdf_markdown."
        print(f"[Postgres][Mermaid][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    # 2. Extrai triplas (N0, N1, N2) distintas do JSON gerado pelo as_is_agent
    #    O n2_id é UUID v5 de (n0_nome|n1_nome|n2_nome) — mesmo algoritmo de
    #    _persistir_no_postgres — garantindo que a FK n2_mermaid → n2_processo
    #    seja satisfeita.
    raw_json = tool_context.state.get("pdf_input_json", "")
    json_str = _extract_json_from_text(raw_json)
    if not json_str:
        msg = "State 'pdf_input_json' inválido — não foi possível extrair N2s."
        print(f"[Postgres][Mermaid][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        msg = f"JSON inválido ao extrair N2s: {e}"
        print(f"[Postgres][Mermaid][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    n2_paths = sorted({
        (
            (r.get("N0") or "").strip(),
            (r.get("N1") or "").strip(),
            (r.get("N2") or "").strip(),
        )
        for r in data.get("rows", [])
        if (r.get("N2") or "").strip()
    })

    if not n2_paths:
        msg = "Nenhum N2 encontrado no JSON para persistir o Mermaid."
        print(f"[Postgres][Mermaid][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    # 3. Persiste no Postgres (síncrono via thread para não bloquear o event loop)
    try:
        mensagem = await asyncio.wait_for(
            run_in_app_executor(_upsert_mermaid_sync, n2_paths, mermaid_script),
            timeout=_MERMAID_TIMEOUT,
        )
        return {"status": "success", "message": mensagem}

    except asyncio.TimeoutError:
        msg = (
            f"Timeout ao persistir Mermaid no Postgres após {_MERMAID_TIMEOUT}s. "
            "Verifique a conectividade e a permissão da service account."
        )
        print(f"[Postgres][ERROR] {msg}", file=sys.stderr)
        return {"status": "error", "message": msg}

    except Exception as e:
        msg = f"Erro ao persistir Mermaid no Postgres: {e}"
        print(f"[Postgres][ERROR] {msg}", file=sys.stderr)
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
            run_in_app_executor(_build_pdf, cleaned),
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

    # ── Persistência do Mermaid no Postgres (side-effect garantido) ───────────
    # Executado aqui para garantir persistência independentemente do LLM
    # chamar save_mermaid_from_state como passo separado.
    mermaid_status = "não realizada"
    try:
        mermaid_script = _extract_mermaid_block(markdown_content)
        if not mermaid_script:
            mermaid_status = "bloco ```mermaid não encontrado no Markdown"
            print(f"[Postgres][Mermaid][WARN] {mermaid_status}", file=sys.stderr)
        else:
            raw_json = tool_context.state.get("pdf_input_json", "")
            json_str = _extract_json_from_text(raw_json)
            if not json_str:
                mermaid_status = "pdf_input_json ausente ou inválido"
                print(f"[Postgres][Mermaid][WARN] {mermaid_status}", file=sys.stderr)
            else:
                data = json.loads(json_str)
                n2_paths = sorted({
                    (
                        (r.get("N0") or "").strip(),
                        (r.get("N1") or "").strip(),
                        (r.get("N2") or "").strip(),
                    )
                    for r in data.get("rows", [])
                    if (r.get("N2") or "").strip()
                })
                if not n2_paths:
                    mermaid_status = "nenhum N2 encontrado no JSON"
                    print(f"[Postgres][Mermaid][WARN] {mermaid_status}", file=sys.stderr)
                else:
                    mermaid_status = await asyncio.wait_for(
                        run_in_app_executor(_upsert_mermaid_sync, n2_paths, mermaid_script),
                        timeout=_MERMAID_TIMEOUT,
                    )
    except asyncio.TimeoutError:
        mermaid_status = f"timeout após {_MERMAID_TIMEOUT}s"
        print(f"[Postgres][Mermaid][ERROR] {mermaid_status}", file=sys.stderr)
    except Exception as _e:
        mermaid_status = f"erro: {_e}"
        print(f"[Postgres][Mermaid][ERROR] {mermaid_status}", file=sys.stderr)

    return {
        "status": "success",
        "message": (
            f"Arquivo 'documento_processo.pdf' (versão {version}) gerado com sucesso "
            "e disponível para download."
        ),
    }


async def generate_pdf_tobe_from_state(tool_context: ToolContext) -> Dict[str, Any]:
    """
    Lê o markdown TO-BE do state (tobe_markdown) e gera o artefato documento_processo_tobe.pdf.
    Esta tool é determinística: não depende do LLM, apenas lê o state e gera o arquivo.
    Deve ser chamada APÓS o tobe_subagent concluir.

    Returns:
        Dicionário com status da operação.
    """
    markdown_content = tool_context.state.get("tobe_markdown", "")

    if not markdown_content or not markdown_content.strip():
        return {
            "status": "error",
            "message": "State 'tobe_markdown' está vazio. O tobe_subagent não gerou o markdown.",
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
            run_in_app_executor(_build_pdf, cleaned),
            timeout=_PDF_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {"status": "error", "message": f"Timeout ao gerar PDF TO-BE após {_PDF_TIMEOUT}s."}
    except Exception as e:
        return {"status": "error", "message": f"Erro ao gerar PDF TO-BE: {str(e)[:200]}"}

    if not pdf_bytes or len(pdf_bytes) == 0:
        return {"status": "error", "message": "PDF TO-BE gerado está vazio."}

    artifact_part = Part(
        inline_data=Blob(
            data=pdf_bytes,
            mime_type="application/pdf",
        )
    )

    version = await tool_context.save_artifact(
        filename="documento_processo_tobe.pdf",
        artifact=artifact_part,
    )

    # ── Persistência do TO-BE no Postgres (side-effect garantido) ─────────────
    # Mesmo padrão de generate_pdf_from_state para o Mermaid: garante que o
    # documento TO-BE não se perca mesmo que o LLM não peça persistência
    # explicitamente. Reaproveita os N2s extraídos do JSON AS-IS (pdf_input_json)
    # pois o TO-BE é gerado a partir do mesmo processo AS-IS.
    tobe_persist_status = "não realizada"
    try:
        raw_json = tool_context.state.get("pdf_input_json", "")
        json_str = _extract_json_from_text(raw_json)
        if not json_str:
            tobe_persist_status = "pdf_input_json ausente ou inválido"
            print(f"[Postgres][TO-BE][WARN] {tobe_persist_status}", file=sys.stderr)
        else:
            data = json.loads(json_str)
            n2_paths = sorted({
                (
                    (r.get("N0") or "").strip(),
                    (r.get("N1") or "").strip(),
                    (r.get("N2") or "").strip(),
                )
                for r in data.get("rows", [])
                if (r.get("N2") or "").strip()
            })
            if not n2_paths:
                tobe_persist_status = "nenhum N2 encontrado no JSON"
                print(f"[Postgres][TO-BE][WARN] {tobe_persist_status}", file=sys.stderr)
            else:
                tobe_persist_status = await asyncio.wait_for(
                    run_in_app_executor(_upsert_tobe_sync, n2_paths, cleaned),
                    timeout=_TOBE_TIMEOUT,
                )
    except asyncio.TimeoutError:
        tobe_persist_status = f"timeout após {_TOBE_TIMEOUT}s"
        print(f"[Postgres][TO-BE][ERROR] {tobe_persist_status}", file=sys.stderr)
    except Exception as _e:
        tobe_persist_status = f"erro: {_e}"
        print(f"[Postgres][TO-BE][ERROR] {tobe_persist_status}", file=sys.stderr)

    return {
        "status": "success",
        "message": (
            f"Arquivo 'documento_processo_tobe.pdf' (versão {version}) gerado com sucesso "
            "e disponível para download."
        ),
    }

