"""
Tool determinística para persistência do JSON canônico N0–N4 no Google Cloud Spanner.

Schema real do banco db-agente-processo (instância id-agente-processo):
  Nós   : N0_Frente, N1_MacroProcesso, N2_Processo, N3_Tarefa, N4_Etapa, N5_Atributos
  Arestas: Edge_Has_N1, Edge_Has_N2, Edge_Has_N3, Edge_Has_N4, Edge_Has_N5
  Grafo  : ProcessosGraph

Cada "row" do JSON é decomposta nos 6 nós e nas 5 arestas que formam
o grafo hierárquico N0->N1->N2->N3->N4->N5.

IDs são gerados de forma determinística via UUID v5 a partir do caminho
completo do nó (ex: "N0|N1|N2|N3|N4"), garantindo idempotência: rodar
duas vezes com o mesmo JSON não duplica dados (insert_or_update / upsert).

Padrões de autenticação — idênticos ao Firestore já existente no projeto:
  1. credentials.json local  -  desenvolvimento
  2. Application Default Credentials - Vertex AI Agent Engine / Cloud Run
     (service account precisa de roles/spanner.databaseUser)
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Desabilita o exporter de métricas internas do Spanner (OpenTelemetry → Cloud
# Monitoring). Sem este flag, o SDK tenta gravar métricas em cada operação e
# bloqueia indefinidamente quando a service account não tem
# roles/monitoring.metricWriter — travando toda a execução.
os.environ.setdefault("SPANNER_ENABLE_BUILTIN_METRICS", "false")

from google.cloud import spanner
from google.oauth2 import service_account
from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

# --- Configurações -----------------------------------------------------------
_CREDENTIALS_PATH = Path(__file__).resolve().parents[2] / "credentials.json"
_GCP_PROJECT      = "steady-computer-487217-p6"
_SPANNER_INSTANCE = "id-agente-processo"
_SPANNER_DATABASE = "db-agente-processo"
_SPANNER_TIMEOUT  = 60  # segundos

# Namespace UUID fixo para geração determinística de IDs
_UUID_NAMESPACE = uuid.UUID("d3a7e1b0-9c4f-4e2a-8f1d-5b6c7e8f9a0b")

# --- Singleton do Database Spanner -------------------------------------------
_spanner_db: Optional[Any] = None


def _get_database():
    """Retorna o singleton do Database Spanner, criando-o na primeira chamada.

    Prioridade de autenticação — igual ao padrão Firestore do projeto:
      1. credentials.json local (desenvolvimento)
      2. ADC injetado automaticamente no Vertex AI Agent Engine / Cloud Run

    O objeto Database do Spanner é thread-safe; não é necessário Lock.
    """
    global _spanner_db
    if _spanner_db is not None:
        return _spanner_db

    if _CREDENTIALS_PATH.exists():
        credentials = service_account.Credentials.from_service_account_file(
            str(_CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        client = spanner.Client(project=_GCP_PROJECT, credentials=credentials)
        logger.info("[Spanner] Autenticado via credentials.json")
    else:
        # Vertex AI Agent Engine / Cloud Run: ADC automático.
        # A service account precisa de roles/spanner.databaseUser.
        client = spanner.Client(project=_GCP_PROJECT)
        logger.info("[Spanner] Autenticado via ADC")

    _spanner_db = client.instance(_SPANNER_INSTANCE).database(_SPANNER_DATABASE)
    return _spanner_db


# --- Utilitários internos ----------------------------------------------------

def _extract_json_from_text(text: str) -> Optional[str]:
    """Extrai o bloco JSON de um texto que pode conter prefixo/sufixo do LLM.

    Mesmo padrão usado em generate_artifacts.py: busca o primeiro '{' e o
    último '}', valida que o resultado contém 'rows'.
    """
    if not text or not text.strip():
        return None

    first_brace = text.find("{")
    last_brace  = text.rfind("}")

    if first_brace == -1 or last_brace == -1 or last_brace <= first_brace:
        return None

    candidate = text[first_brace: last_brace + 1]

    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict) and "rows" in parsed:
            return candidate
    except json.JSONDecodeError:
        pass

    return None


def _as_list(value: Any) -> List[str]:
    """Garante lista de strings para colunas ARRAY<STRING(MAX)> do Spanner."""
    if isinstance(value, list):
        return [str(v) for v in value if v is not None]
    if value is None or value == "":
        return []
    return [str(value)]


# --- Geração de IDs determinísticos ------------------------------------------

def _make_id(*partes: str) -> str:
    """Gera um UUID v5 determinístico a partir do caminho hierárquico do nó.

    Exemplo: _make_id("Frente A", "Macro B") -> UUID único para N1 "Macro B"
    dentro de "Frente A". Rodar duas vezes com os mesmos dados gera o mesmo ID,
    garantindo idempotência (insert_or_update não duplica registros).
    """
    chave = "|".join(partes)
    return str(uuid.uuid5(_UUID_NAMESPACE, chave))


# --- Persistência síncrona (roda em asyncio.to_thread) -----------------------

def _persistir_no_spanner(dados: dict) -> str:
    """Decompõe o JSON canônico N0–N4 e persiste no grafo do Spanner.

    Para cada "row" do JSON:
      - Extrai os nós N0..N4 e gera IDs determinísticos (UUID v5)
      - Cria o nó N5_Atributos com os campos de detalhe da etapa
      - Cria as arestas Edge_Has_N1..Edge_Has_N5

    Usa insert_or_update (upsert) em batch para todas as tabelas,
    garantindo idempotência: o mesmo JSON pode ser processado mais
    de uma vez sem gerar registros duplicados.

    Args:
        dados: Dicionário Python já parseado do JSON canônico.

    Returns:
        Mensagem de resumo com o total de linhas processadas.
    """
    rows: List[dict] = dados.get("rows", [])
    if not rows:
        return "JSON não contém linhas ('rows') para inserir no Spanner."

    database = _get_database()

    # Dicts keyed by id para deduplicação dentro do mesmo batch
    n0_nodes:  Dict[str, Tuple] = {}
    n1_nodes:  Dict[str, Tuple] = {}
    n2_nodes:  Dict[str, Tuple] = {}
    n3_nodes:  Dict[str, Tuple] = {}
    n4_nodes:  Dict[str, Tuple] = {}
    n5_nodes:  Dict[str, Tuple] = {}

    edges_n1: Set[Tuple[str, str]] = set()
    edges_n2: Set[Tuple[str, str]] = set()
    edges_n3: Set[Tuple[str, str]] = set()
    edges_n4: Set[Tuple[str, str]] = set()
    edges_n5: Set[Tuple[str, str]] = set()

    for row in rows:
        n0_nome = (row.get("N0") or "").strip()
        n1_nome = (row.get("N1") or "").strip()
        n2_nome = (row.get("N2") or "").strip()
        n3_nome = (row.get("N3") or "").strip()
        n4_nome = (row.get("N4") or "").strip()

        # IDs determinísticos: caminho completo evita colisão entre nós com
        # o mesmo nome em ramos diferentes da hierarquia.
        n0_id = _make_id(n0_nome)
        n1_id = _make_id(n0_nome, n1_nome)
        n2_id = _make_id(n0_nome, n1_nome, n2_nome)
        n3_id = _make_id(n0_nome, n1_nome, n2_nome, n3_nome)
        n4_id = _make_id(n0_nome, n1_nome, n2_nome, n3_nome, n4_nome)
        n5_id = _make_id(n0_nome, n1_nome, n2_nome, n3_nome, n4_nome, "attrs")

        # Nós
        n0_nodes[n0_id] = (n0_id, n0_nome)
        n1_nodes[n1_id] = (n1_id, n1_nome)
        n2_nodes[n2_id] = (n2_id, n2_nome)
        n3_nodes[n3_id] = (n3_id, n3_nome)
        n4_nodes[n4_id] = (n4_id, n4_nome)

        # N5_Atributos: ARRAY<STRING(MAX)> -> lista Python (aceito pelo SDK)
        n5_nodes[n5_id] = (
            n5_id,
            (row.get("descricao") or ""),
            _as_list(row.get("entradas")),
            _as_list(row.get("saidas")),
            _as_list(row.get("sistemasEnvolvidos")),
            _as_list(row.get("kpis")),
            _as_list(row.get("oportunidadesMelhoria")),
        )

        # Arestas
        edges_n1.add((n0_id, n1_id))
        edges_n2.add((n1_id, n2_id))
        edges_n3.add((n2_id, n3_id))
        edges_n4.add((n3_id, n4_id))
        edges_n5.add((n4_id, n5_id))

    # Batch único: insert_or_update (upsert) em todas as tabelas
    with database.batch() as batch:

        if n0_nodes:
            batch.insert_or_update(
                table="N0_Frente",
                columns=["id", "nome"],
                values=list(n0_nodes.values()),
            )

        if n1_nodes:
            batch.insert_or_update(
                table="N1_MacroProcesso",
                columns=["id", "nome"],
                values=list(n1_nodes.values()),
            )

        if n2_nodes:
            batch.insert_or_update(
                table="N2_Processo",
                columns=["id", "nome"],
                values=list(n2_nodes.values()),
            )

        if n3_nodes:
            batch.insert_or_update(
                table="N3_Tarefa",
                columns=["id", "nome"],
                values=list(n3_nodes.values()),
            )

        if n4_nodes:
            batch.insert_or_update(
                table="N4_Etapa",
                columns=["id", "nome"],
                values=list(n4_nodes.values()),
            )

        if n5_nodes:
            batch.insert_or_update(
                table="N5_Atributos",
                columns=[
                    "id",
                    "descricao",
                    "entradas",
                    "saidas",
                    "sistemas_envolvidos",
                    "kpis",
                    "oportunidades_melhoria",
                ],
                values=list(n5_nodes.values()),
            )

        if edges_n1:
            batch.insert_or_update(
                table="Edge_Has_N1",
                columns=["n0_id", "n1_id"],
                values=list(edges_n1),
            )

        if edges_n2:
            batch.insert_or_update(
                table="Edge_Has_N2",
                columns=["n1_id", "n2_id"],
                values=list(edges_n2),
            )

        if edges_n3:
            batch.insert_or_update(
                table="Edge_Has_N3",
                columns=["n2_id", "n3_id"],
                values=list(edges_n3),
            )

        if edges_n4:
            batch.insert_or_update(
                table="Edge_Has_N4",
                columns=["n3_id", "n4_id"],
                values=list(edges_n4),
            )

        if edges_n5:
            batch.insert_or_update(
                table="Edge_Has_N5",
                columns=["n4_id", "n5_id"],
                values=list(edges_n5),
            )

    return (
        f"Spanner: {len(rows)} etapa(s) persistida(s) no grafo ProcessosGraph "
        f"({len(n0_nodes)} Frente(s), {len(n1_nodes)} MacroProcesso(s), "
        f"{len(n2_nodes)} Processo(s), {len(n3_nodes)} Tarefa(s), "
        f"{len(n4_nodes)} Etapa(s)) — "
        f"database: {_SPANNER_DATABASE}, instância: {_SPANNER_INSTANCE}."
    )


# --- Tool ADK ----------------------------------------------------------------

async def save_to_spanner_from_state(tool_context: ToolContext) -> str:
    """Tool determinística: lê o JSON do state e persiste no grafo Spanner.

    Lê state["pdf_input_json"], extrai o JSON canônico N0–N4 e popula
    as 11 tabelas do grafo ProcessosGraph no database db-agente-processo:
      - 6 tabelas de nós  : N0_Frente, N1_MacroProcesso, N2_Processo,
                            N3_Tarefa, N4_Etapa, N5_Atributos
      - 5 tabelas de arestas: Edge_Has_N1, Edge_Has_N2, Edge_Has_N3,
                               Edge_Has_N4, Edge_Has_N5

    Idempotente: processar o mesmo documento mais de uma vez não duplica dados
    (insert_or_update com IDs determinísticos UUID v5 por caminho hierárquico).
    Um único batch → uma única chamada de rede → sem risco de trava.

    Returns:
        String com resumo da operação (status + totais).
    """
    raw_json = tool_context.state.get("pdf_input_json", "")

    if not raw_json or not raw_json.strip():
        msg = "[Spanner] AVISO: state 'pdf_input_json' está vazio. Persistência ignorada."
        logger.warning(msg)
        return msg

    json_str = _extract_json_from_text(raw_json)
    if not json_str:
        msg = (
            "[Spanner] ERRO: não foi possível extrair JSON válido do state. "
            f"Conteúdo (primeiros 200 chars): {raw_json[:200]}"
        )
        logger.error(msg)
        return msg

    try:
        dados = json.loads(json_str)
    except json.JSONDecodeError as e:
        msg = f"[Spanner] ERRO: JSON inválido ao salvar no Spanner: {e}"
        logger.error(msg)
        return msg

    try:
        mensagem = await asyncio.wait_for(
            asyncio.to_thread(_persistir_no_spanner, dados),
            timeout=_SPANNER_TIMEOUT,
        )
        logger.info(mensagem)
        return mensagem

    except asyncio.TimeoutError:
        msg = (
            f"[Spanner] ERRO: timeout de {_SPANNER_TIMEOUT}s ao persistir no Spanner. "
            "Verifique a conectividade e a permissão da service account "
            f"(roles/spanner.databaseUser na instância {_SPANNER_INSTANCE})."
        )
        logger.error(msg)
        return msg

    except Exception as e:
        msg = f"[Spanner] ERRO ao persistir no Spanner: {e}"
        logger.exception(msg)
        return msg


# --- Persistência do Mermaid -------------------------------------------------

def _upsert_mermaid_sync(n0_nomes: List[str], mermaid_script: str) -> str:
    """Faz upsert do script Mermaid para cada N0 no Spanner (síncrono).

    Persiste em N0_Mermaid e Edge_Has_Mermaid num único batch.
    Idempotente via insert_or_update — reprocessamentos sobrescrevem sem duplicar.
    Deve ser chamado via asyncio.to_thread() para não bloquear o event loop.

    Args:
        n0_nomes: Lista de nomes de N0 (Frente) para associar ao script.
        mermaid_script: Script Mermaid puro (sem code fences).

    Returns:
        Mensagem de resumo da operação.
    """
    if not n0_nomes:
        return "[Spanner][Mermaid] Nenhum N0 fornecido — persistência ignorada."
    if not mermaid_script or not mermaid_script.strip():
        return "[Spanner][Mermaid] Script Mermaid vazio — persistência ignorada."

    database = _get_database()

    mermaid_rows: List[Tuple] = []
    edge_rows: List[Tuple] = []
    for n0_nome in n0_nomes:
        n0_id = _make_id(n0_nome)
        mermaid_rows.append((n0_id, mermaid_script, spanner.COMMIT_TIMESTAMP))
        edge_rows.append((n0_id, n0_id))  # mermaid_id == n0_id (mesma PK)

    with database.batch() as batch:
        batch.insert_or_update(
            table="N0_Mermaid",
            columns=["n0_id", "mermaid_script", "gerado_em"],
            values=mermaid_rows,
        )
        batch.insert_or_update(
            table="Edge_Has_Mermaid",
            columns=["n0_id", "mermaid_id"],
            values=edge_rows,
        )

    nomes_resumo = ", ".join(f"'{n}'" for n in n0_nomes[:5])
    sufixo = f" ... e mais {len(n0_nomes) - 5}" if len(n0_nomes) > 5 else ""
    return (
        f"[Spanner][Mermaid] Script persistido para {len(n0_nomes)} N0(s): "
        f"{nomes_resumo}{sufixo}. "
        f"Tabelas: N0_Mermaid + Edge_Has_Mermaid "
        f"(database: {_SPANNER_DATABASE}, instância: {_SPANNER_INSTANCE})."
    )
