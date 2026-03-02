import asyncio
import json
import os
import tempfile
from typing import Dict, Any

from google.genai.types import Part, Blob

from agent_tools.json_to_xlsx import json_to_xlsx

# Timeout (segundos) para a geração do XLSX
_XLSX_TIMEOUT = 60


def _build_xlsx(json_str: str) -> bytes:
    """
    Executa toda a I/O síncrona e o processamento pesado (pandas + openpyxl).
    Deve ser chamada via asyncio.to_thread() para não bloquear o event loop.

    Returns:
        Bytes do arquivo XLSX gerado.
    """
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


async def generate_xlsx_tool(
    json_str: str,
    tool_context,
) -> Dict[str, Any]:
    """
    Converte o JSON canônico gerado pelo agente (formato columns/rows) em um
    arquivo Excel (.xlsx) e o disponibiliza como artefato para download.
    O processamento pesado (pandas + openpyxl) é executado em uma thread
    separada para não bloquear o event loop do ADK.

    Args:
        json_str: String JSON no formato canônico (com campos 'columns' e 'rows').

    Returns:
        Dicionário com status da operação.
    """
    try:
        json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON inválido: {e}"}

    try:
        # Executa toda a I/O síncrona e CPU em thread separada
        xlsx_bytes = await asyncio.wait_for(
            asyncio.to_thread(_build_xlsx, json_str),
            timeout=_XLSX_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "message": f"Timeout ao gerar XLSX após {_XLSX_TIMEOUT}s.",
        }
    except Exception as e:
        return {"status": "error", "message": f"Erro ao gerar XLSX: {e}"}

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
            "e disponível para download."
        ),
    }
