"""
Tool determinística para persistência do JSON canônico N0–N4 no Cloud SQL for
PostgreSQL.

Schema real do banco db-agente-processo (instância agente-processos-db):
  Nós   : n0_frente, n1_macroprocesso, n2_processo, n3_tarefa, n4_etapa, n5_atributos
  Arestas: edge_has_n1, edge_has_n2, edge_has_n3, edge_has_n4, edge_has_n5
  DDL completo em installation_scripts/postgres_schema.sql

Cada "row" do JSON é decomposta nos 6 nós e nas 5 arestas que formam
o grafo hierárquico N0->N1->N2->N3->N4->N5.

IDs são gerados de forma determinística via UUID v5 a partir do caminho
completo do nó (ex: "N0|N1|N2|N3|N4"), garantindo idempotência: rodar
duas vezes com o mesmo JSON não duplica dados (INSERT ... ON CONFLICT
DO UPDATE / upsert).

Conexão via Cloud SQL Python Connector — sem necessidade de Auth Proxy:
Padrões de autenticação — idênticos ao Firestore já existente no projeto:
  1. credentials.json local  -  desenvolvimento
  2. Application Default Credentials - Vertex AI Agent Engine / Cloud Run
     (service account precisa de roles/cloudsql.client)

Credenciais do banco (usuário/senha) vêm de variáveis de ambiente
(POSTGRES_USER / POSTGRES_PASSWORD) — nunca hardcoded.
"""

import asyncio
import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from google.cloud.sql.connector import Connector
from google.oauth2 import service_account
from google.adk.tools.tool_context import ToolContext

from document_processor.tools.executor import run_in_app_executor

logger = logging.getLogger(__name__)

# --- Configurações -----------------------------------------------------------
_CREDENTIALS_PATH = Path(__file__).resolve().parents[2] / "credentials.json"
_GCP_PROJECT              = "steady-computer-487217-p6"
_INSTANCE_CONNECTION_NAME = "steady-computer-487217-p6:us-east1:agente-processos-db"
_DB_NAME                  = "db-agente-processo"
_DB_USER                  = os.getenv("POSTGRES_USER", "postgres")
_DB_PASSWORD              = os.getenv("POSTGRES_PASSWORD", "")
_POSTGRES_TIMEOUT         = 60  # segundos

# Namespace UUID fixo para geração determinística de IDs
_UUID_NAMESPACE = uuid.UUID("d3a7e1b0-9c4f-4e2a-8f1d-5b6c7e8f9a0b")

# --- Singleton do Connector ----------------------------------------------------
_connector: Optional[Connector] = None
_connector_lock = threading.Lock()


def _get_connector() -> Connector:
    """Retorna o singleton do Cloud SQL Python Connector, criando-o na primeira chamada.

    Prioridade de autenticação — igual ao padrão Firestore do projeto:
      1. credentials.json local (desenvolvimento)
      2. ADC injetado automaticamente no Vertex AI Agent Engine / Cloud Run

    Usa threading.Lock para garantir que apenas uma instância seja criada
    mesmo em ambientes com múltiplos threads concorrentes.
    """
    global _connector
    with _connector_lock:
        if _connector is not None:
            return _connector

        # timeout=15: bound explícito para as operações internas do Connector
        # (handshake/cert fetch). Não é garantia absoluta contra travamento —
        # a chamada síncrona connector.connect() ainda pode bloquear a thread
        # indefinidamente em cenários de rede que descartam pacotes em
        # silêncio (ver investigação de 2026-08-06) — por isso essa chamada
        # roda isolada em run_in_app_executor, nunca no pool padrão do
        # processo, para não arriscar sufocar o que o google-genai precisa.
        if _CREDENTIALS_PATH.exists():
            credentials = service_account.Credentials.from_service_account_file(
                str(_CREDENTIALS_PATH),
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            _connector = Connector(credentials=credentials, timeout=15)
            logger.info("[Postgres] Autenticado via credentials.json")
        else:
            # Vertex AI Agent Engine / Cloud Run: ADC automático.
            # A service account precisa de roles/cloudsql.client.
            _connector = Connector(timeout=15)
            logger.info("[Postgres] Autenticado via ADC")

    return _connector


# Pré-aquece o Connector no import do módulo (startup do worker), não na
# primeira gravação de um turno real de usuário — mesmo raciocínio do
# pré-aquecimento do client do Firestore (ver generate_artifacts.py e a
# investigação do incidente de travamento intermitente, 2026-08-06).
try:
    _get_connector()
    logger.info("[Postgres] Connector pré-aquecido no import do módulo.")
except Exception as _exc:
    logger.warning("[Postgres] Falha ao pré-aquecer Connector no import: %s", _exc)


_CONNECT_TIMEOUT = 20  # segundos — ver docstring de _get_connection abaixo


def _get_connection():
    """Abre uma nova conexão pg8000 via Cloud SQL Python Connector.

    Réplica manual do que `connector.connect()` (público) faz internamente —
    `asyncio.run_coroutine_threadsafe(connector.connect_async(...), connector._loop)`
    seguido de `.result()` — mas passando um timeout REAL para o `.result()`.
    `connector.connect()` público não expõe essa opção e trava
    indefinidamente se a conexão TCP/TLS nunca retornar (raiz identificada na
    investigação do incidente de travamento intermitente, 2026-08-06:
    `connector.connect()` bloqueia sem timeout, e o socket TCP subjacente
    também não tem timeout).

    Importante: `concurrent.futures.Future.result(timeout=N)` usa espera
    bloqueante em nível de SO (`threading.Condition.wait`), que NÃO depende
    do event loop do asyncio estar sendo escalonado para dessa desistir —
    mais robusto que `asyncio.wait_for`, que comprovadamente não conseguiu
    interromper esse tipo de travamento em produção (mesmo isolado em
    `run_in_app_executor`, ver `agente_processos_chat_hang_incident`).

    Uma conexão nova por operação evita problemas de thread-safety quando
    várias tools rodam concorrentemente. O túnel TLS é gerenciado pelo
    Connector (singleton reaproveitado, com seu próprio loop interno em
    background thread — ver `Connector.__init__`).
    """
    connector = _get_connector()
    future = asyncio.run_coroutine_threadsafe(
        connector.connect_async(
            _INSTANCE_CONNECTION_NAME,
            "pg8000",
            user=_DB_USER,
            password=_DB_PASSWORD,
            db=_DB_NAME,
        ),
        connector._loop,
    )
    return future.result(timeout=_CONNECT_TIMEOUT)


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
    """Garante lista de strings para colunas TEXT[] do Postgres."""
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
    garantindo idempotência (upsert não duplica registros).
    """
    chave = "|".join(partes)
    return str(uuid.uuid5(_UUID_NAMESPACE, chave))


# --- Persistência síncrona (roda em asyncio.to_thread) -----------------------

def _persistir_no_postgres(dados: dict) -> str:
    """Decompõe o JSON canônico N0–N4 e persiste no grafo do Postgres.

    Para cada "row" do JSON:
      - Extrai os nós N0..N4 e gera IDs determinísticos (UUID v5)
      - Cria o nó n5_atributos com os campos de detalhe da etapa
      - Cria as arestas edge_has_n1..edge_has_n5

    Usa INSERT ... ON CONFLICT DO UPDATE (upsert) em batch para todas as
    tabelas, garantindo idempotência: o mesmo JSON pode ser processado mais
    de uma vez sem gerar registros duplicados. Uma única transação (commit
    ao final) — sem risco de estado parcial.

    Args:
        dados: Dicionário Python já parseado do JSON canônico.

    Returns:
        Mensagem de resumo com o total de linhas processadas.
    """
    rows: List[dict] = dados.get("rows", [])
    if not rows:
        return "JSON não contém linhas ('rows') para inserir no Postgres."

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

        # n5_atributos: TEXT[] -> lista Python (aceito nativamente pelo pg8000)
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

    conn = _get_connection()
    cur = conn.cursor()
    try:
        if n0_nodes:
            cur.executemany(
                "INSERT INTO n0_frente (id, nome) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome",
                list(n0_nodes.values()),
            )

        if n1_nodes:
            cur.executemany(
                "INSERT INTO n1_macroprocesso (id, nome) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome",
                list(n1_nodes.values()),
            )

        if n2_nodes:
            cur.executemany(
                "INSERT INTO n2_processo (id, nome) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome",
                list(n2_nodes.values()),
            )

        if n3_nodes:
            cur.executemany(
                "INSERT INTO n3_tarefa (id, nome) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome",
                list(n3_nodes.values()),
            )

        if n4_nodes:
            cur.executemany(
                "INSERT INTO n4_etapa (id, nome) VALUES (%s, %s) "
                "ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome",
                list(n4_nodes.values()),
            )

        if n5_nodes:
            cur.executemany(
                """
                INSERT INTO n5_atributos (
                    id, descricao, entradas, saidas,
                    sistemas_envolvidos, kpis, oportunidades_melhoria
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    descricao               = EXCLUDED.descricao,
                    entradas                = EXCLUDED.entradas,
                    saidas                  = EXCLUDED.saidas,
                    sistemas_envolvidos     = EXCLUDED.sistemas_envolvidos,
                    kpis                    = EXCLUDED.kpis,
                    oportunidades_melhoria  = EXCLUDED.oportunidades_melhoria
                """,
                list(n5_nodes.values()),
            )

        if edges_n1:
            cur.executemany(
                "INSERT INTO edge_has_n1 (n0_id, n1_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                list(edges_n1),
            )

        if edges_n2:
            cur.executemany(
                "INSERT INTO edge_has_n2 (n1_id, n2_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                list(edges_n2),
            )

        if edges_n3:
            cur.executemany(
                "INSERT INTO edge_has_n3 (n2_id, n3_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                list(edges_n3),
            )

        if edges_n4:
            cur.executemany(
                "INSERT INTO edge_has_n4 (n3_id, n4_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                list(edges_n4),
            )

        if edges_n5:
            cur.executemany(
                "INSERT INTO edge_has_n5 (n4_id, n5_id) VALUES (%s, %s) "
                "ON CONFLICT DO NOTHING",
                list(edges_n5),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    return (
        f"Postgres: {len(rows)} etapa(s) persistida(s) no grafo ProcessosGraph "
        f"({len(n0_nodes)} Frente(s), {len(n1_nodes)} MacroProcesso(s), "
        f"{len(n2_nodes)} Processo(s), {len(n3_nodes)} Tarefa(s), "
        f"{len(n4_nodes)} Etapa(s)) — "
        f"database: {_DB_NAME}, instância: {_INSTANCE_CONNECTION_NAME}."
    )


# --- Tool ADK ----------------------------------------------------------------

async def save_to_postgres_from_state(tool_context: ToolContext) -> str:
    """Tool determinística: lê o JSON do state e persiste no grafo Postgres.

    Lê state["pdf_input_json"], extrai o JSON canônico N0–N4 e popula
    as 13 tabelas do grafo ProcessosGraph no database db-agente-processo:
      - 6 tabelas de nós  : n0_frente, n1_macroprocesso, n2_processo,
                            n3_tarefa, n4_etapa, n5_atributos
      - 5 tabelas de arestas: edge_has_n1, edge_has_n2, edge_has_n3,
                               edge_has_n4, edge_has_n5

    Idempotente: processar o mesmo documento mais de uma vez não duplica dados
    (upsert com IDs determinísticos UUID v5 por caminho hierárquico).
    Uma única transação → sem risco de estado parcial.

    Returns:
        String com resumo da operação (status + totais).
    """
    raw_json = tool_context.state.get("pdf_input_json", "")

    if not raw_json or not raw_json.strip():
        msg = "[Postgres] AVISO: state 'pdf_input_json' está vazio. Persistência ignorada."
        logger.warning(msg)
        return msg

    json_str = _extract_json_from_text(raw_json)
    if not json_str:
        msg = (
            "[Postgres] ERRO: não foi possível extrair JSON válido do state. "
            f"Conteúdo (primeiros 200 chars): {raw_json[:200]}"
        )
        logger.error(msg)
        return msg

    try:
        dados = json.loads(json_str)
    except json.JSONDecodeError as e:
        msg = f"[Postgres] ERRO: JSON inválido ao salvar no Postgres: {e}"
        logger.error(msg)
        return msg

    try:
        mensagem = await asyncio.wait_for(
            run_in_app_executor(_persistir_no_postgres, dados),
            timeout=_POSTGRES_TIMEOUT,
        )
        logger.info(mensagem)
        return mensagem

    except asyncio.TimeoutError:
        msg = (
            f"[Postgres] ERRO: timeout de {_POSTGRES_TIMEOUT}s ao persistir no Postgres. "
            "Verifique a conectividade e a permissão da service account "
            f"(roles/cloudsql.client na instância {_INSTANCE_CONNECTION_NAME})."
        )
        logger.error(msg)
        return msg

    except Exception as e:
        msg = f"[Postgres] ERRO ao persistir no Postgres: {e}"
        logger.exception(msg)
        return msg


# --- Persistência do Mermaid -------------------------------------------------

def _upsert_mermaid_sync(n2_paths: List[Tuple[str, str, str]], mermaid_script: str) -> str:
    """Faz upsert do script Mermaid para cada n2_processo no Postgres (síncrono).

    Persiste em n2_mermaid e edge_has_mermaid numa única transação.
    Idempotente via ON CONFLICT DO UPDATE — reprocessamentos sobrescrevem sem duplicar.
    Deve ser chamado via asyncio.to_thread() para não bloquear o event loop.

    A PK de n2_mermaid é o próprio n2_id, garantindo 1 e somente 1 script
    por n2_processo.

    IMPORTANTE: o n2_id é gerado com _make_id(n0_nome, n1_nome, n2_nome),
    idêntico ao caminho hierárquico usado em _persistir_no_postgres, garantindo
    que a FK n2_mermaid → n2_processo seja satisfeita.

    Args:
        n2_paths: Lista de triplas (n0_nome, n1_nome, n2_nome) que identificam
                  univocamente cada n2_processo.
        mermaid_script: Script Mermaid puro (sem code fences).

    Returns:
        Mensagem de resumo da operação.
    """
    if not n2_paths:
        return "[Postgres][Mermaid] Nenhum N2 fornecido — persistência ignorada."
    if not mermaid_script or not mermaid_script.strip():
        return "[Postgres][Mermaid] Script Mermaid vazio — persistência ignorada."

    mermaid_rows: List[Tuple] = []
    edge_rows: List[Tuple] = []
    for (n0_nome, n1_nome, n2_nome) in n2_paths:
        # Mesmo algoritmo de _persistir_no_postgres — garante mesmo UUID
        n2_id = _make_id(n0_nome, n1_nome, n2_nome)
        mermaid_rows.append((n2_id, mermaid_script))
        edge_rows.append((n2_id, n2_id))  # mermaid_id == n2_id (relação 1:1)

    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.executemany(
            "INSERT INTO n2_mermaid (n2_id, mermaid_script, gerado_em) "
            "VALUES (%s, %s, now()) "
            "ON CONFLICT (n2_id) DO UPDATE SET "
            "mermaid_script = EXCLUDED.mermaid_script, gerado_em = now()",
            mermaid_rows,
        )
        cur.executemany(
            "INSERT INTO edge_has_mermaid (n2_id, mermaid_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            edge_rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    nomes_resumo = ", ".join(f"'{p[2]}'" for p in n2_paths[:5])
    sufixo = f" ... e mais {len(n2_paths) - 5}" if len(n2_paths) > 5 else ""
    return (
        f"[Postgres][Mermaid] Script persistido para {len(n2_paths)} N2(s): "
        f"{nomes_resumo}{sufixo}. "
        f"Tabelas: n2_mermaid + edge_has_mermaid "
        f"(database: {_DB_NAME}, instância: {_INSTANCE_CONNECTION_NAME})."
    )


# --- Persistência do TO-BE ----------------------------------------------------

def _upsert_tobe_sync(n2_paths: List[Tuple[str, str, str]], tobe_markdown: str) -> str:
    """Faz upsert do documento Markdown TO-BE para cada n2_processo no Postgres (síncrono).

    Persiste em n2_tobe_documento e edge_has_tobe numa única transação.
    Idempotente via ON CONFLICT DO UPDATE — reprocessamentos sobrescrevem sem duplicar.
    Deve ser chamado via asyncio.to_thread() para não bloquear o event loop.

    Mesmo padrão de _upsert_mermaid_sync: a PK de n2_tobe_documento é o próprio
    n2_id, garantindo 1 e somente 1 documento TO-BE por n2_processo (o mesmo
    documento gerado para a solicitação é associado a cada N2 distinto do AS-IS).

    Args:
        n2_paths: Lista de triplas (n0_nome, n1_nome, n2_nome) que identificam
                  univocamente cada n2_processo.
        tobe_markdown: Markdown puro do documento TO-BE (sem code fences).

    Returns:
        Mensagem de resumo da operação.
    """
    if not n2_paths:
        return "[Postgres][TO-BE] Nenhum N2 fornecido — persistência ignorada."
    if not tobe_markdown or not tobe_markdown.strip():
        return "[Postgres][TO-BE] Markdown TO-BE vazio — persistência ignorada."

    tobe_rows: List[Tuple] = []
    edge_rows: List[Tuple] = []
    for (n0_nome, n1_nome, n2_nome) in n2_paths:
        # Mesmo algoritmo de _persistir_no_postgres — garante mesmo UUID
        n2_id = _make_id(n0_nome, n1_nome, n2_nome)
        tobe_rows.append((n2_id, tobe_markdown))
        edge_rows.append((n2_id, n2_id))  # tobe_id == n2_id (relação 1:1)

    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.executemany(
            "INSERT INTO n2_tobe_documento (n2_id, tobe_markdown, gerado_em) "
            "VALUES (%s, %s, now()) "
            "ON CONFLICT (n2_id) DO UPDATE SET "
            "tobe_markdown = EXCLUDED.tobe_markdown, gerado_em = now()",
            tobe_rows,
        )
        cur.executemany(
            "INSERT INTO edge_has_tobe (n2_id, tobe_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            edge_rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

    nomes_resumo = ", ".join(f"'{p[2]}'" for p in n2_paths[:5])
    sufixo = f" ... e mais {len(n2_paths) - 5}" if len(n2_paths) > 5 else ""
    return (
        f"[Postgres][TO-BE] Documento persistido para {len(n2_paths)} N2(s): "
        f"{nomes_resumo}{sufixo}. "
        f"Tabelas: n2_tobe_documento + edge_has_tobe "
        f"(database: {_DB_NAME}, instância: {_INSTANCE_CONNECTION_NAME})."
    )
