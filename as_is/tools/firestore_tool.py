import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from google.cloud import firestore
from google.oauth2 import service_account

# Caminho para as credenciais na raiz do projeto
_CREDENTIALS_PATH = Path(__file__).resolve().parents[2] / "credentials.json"
_GCP_PROJECT = "steady-computer-487217-p6"
_FIRESTORE_DATABASE = "as-is-processes"
_COLLECTION_NAME = "processos"

# Timeout (segundos) para a operação de escrita no Firestore
_FIRESTORE_TIMEOUT = 30

# Singleton do cliente Firestore — criado uma única vez e reutilizado
_firestore_client: firestore.Client | None = None


def _get_firestore_client() -> firestore.Client:
    """Retorna o cliente Firestore, criando-o apenas na primeira chamada (singleton).
    
    Prioridade de autenticação:
      1. credentials.json local (desenvolvimento)
      2. Application Default Credentials (Vertex AI / Cloud Run)
    """
    global _firestore_client
    if _firestore_client is None:
        if _CREDENTIALS_PATH.exists():
            credentials = service_account.Credentials.from_service_account_file(
                str(_CREDENTIALS_PATH),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            _firestore_client = firestore.Client(
                project=_GCP_PROJECT,
                credentials=credentials,
                database=_FIRESTORE_DATABASE,
            )
        else:
            # Ambiente cloud (Vertex AI Agent Engine / Cloud Run):
            # usa Application Default Credentials injetadas automaticamente
            _firestore_client = firestore.Client(
                project=_GCP_PROJECT,
                database=_FIRESTORE_DATABASE,
            )
    return _firestore_client


def _write_to_firestore(data: dict) -> str:
    """
    Executa a escrita síncrona no Firestore.
    Deve ser chamada via asyncio.to_thread() para não bloquear o event loop.

    Returns:
        ID do documento criado.
    """
    client = _get_firestore_client()
    _, doc_ref = client.collection(_COLLECTION_NAME).add(data)
    return doc_ref.id


async def save_to_firestore_tool(
    json_str: str,
    tool_context,
) -> Dict[str, Any]:
    """
    Persiste o JSON canônico gerado pelo agente no Google Cloud Firestore.

    A coleção utilizada é 'processos' dentro do projeto 'as-is-processes'.
    Cada chamada cria um documento novo com ID gerado automaticamente pelo Firestore.
    A escrita é feita em uma thread separada para não bloquear o event loop do ADK.

    Args:
        json_str: String JSON no formato canônico (com campos schemaVersion, meta,
                  columns, rows e exportHints).

    Returns:
        Dicionário com status da operação e o ID do documento criado.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"JSON inválido: {e}"}

    try:
        # Executa a I/O síncrona do Firestore em uma thread separada,
        # respeitando o timeout definido, sem bloquear o event loop do ADK.
        doc_id = await asyncio.wait_for(
            asyncio.to_thread(_write_to_firestore, data),
            timeout=_FIRESTORE_TIMEOUT,
        )
        return {
            "status": "success",
            "message": (
                f"Dados persistidos com sucesso no Firestore "
                f"(coleção: '{_COLLECTION_NAME}', documento: '{doc_id}')."
            ),
        }
    except asyncio.TimeoutError:
        return {
            "status": "error",
            "message": (
                f"Timeout ao salvar no Firestore após {_FIRESTORE_TIMEOUT}s. "
                "Verifique a conectividade e as credenciais."
            ),
        }
    except Exception as e:
        return {"status": "error", "message": f"Erro ao salvar no Firestore: {e}"}
