"""
╔══════════════════════════════════════════════════════════════════════════════╗
║     MAPA DE PROCESSOS AS-IS  ·  Visualizador de Processos                   ║
║     Cloud SQL for PostgreSQL (db-agente-processo) · Streamlit               ║
╚══════════════════════════════════════════════════════════════════════════════╝

Pesquisa processos na tabela n0_frente do Postgres e exibe a hierarquia
completa N0 → N1 → N2 → N3 → N4 → N5 com Mermaid para o diagrama de fluxo.
"""

from __future__ import annotations

import html
import logging
import os
import re
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# ─── Caminhos / configurações ────────────────────────────────────────────────
_ROOT             = Path(__file__).resolve().parent
_PROJECT_ROOT     = _ROOT.parent          # raiz do projeto (um nível acima de dataViz/)
_CREDENTIALS_PATH = _PROJECT_ROOT / "credentials.json"

# Carrega .env local (dev). Em produção (Cloud Run) as vars vêm de --set-env-vars,
# então load_dotenv() é um no-op silencioso se o arquivo não existir.
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")

_GCP_PROJECT              = "steady-computer-487217-p6"
_INSTANCE_CONNECTION_NAME = "steady-computer-487217-p6:us-east1:agente-processos-db"
_DB_NAME                  = "db-agente-processo"
_DB_USER                  = os.getenv("POSTGRES_USER", "postgres")
_DB_PASSWORD              = os.getenv("POSTGRES_PASSWORD", "")


# ─── CSS Global ──────────────────────────────────────────────────────────────
def _inject_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
            background-color: #F8F9FA;
        }
        .stApp { background-color: #F8F9FA; }
        .main .block-container { padding-top: 1rem; padding-bottom: 2rem; }
        [data-testid="stSidebar"] > div:first-child { padding-top: 1.875rem; }

        /* ── hero header ──────────────────────────────── */
        .hero {
            background: #1a3560;
            border-radius: 6px;
            padding: 0 1.8rem;
            min-height: 82px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            margin-bottom: 1.2rem;
            box-shadow: 0 2px 8px rgba(26,53,96,0.15);
        }
        .hero-title {
            font-size: 1.35rem; font-weight: 700;
            color: #FFFFFF; letter-spacing: -0.2px; margin: 0;
        }
        .hero-subtitle {
            font-size: 0.82rem; color: #a8c0e0;
            margin-top: 0.2rem; font-weight: 400;
        }

        /* ── sidebar ──────────────────────────────────── */
        [data-testid="stSidebar"] {
            background: #FFFFFF;
            border-right: 1px solid #dee2e6;
        }
        [data-testid="stSidebar"] .stMarkdown p,
        [data-testid="stSidebar"] label { color: #495057 !important; }

        .sidebar-header {
            background: #1a3560;
            border-radius: 6px;
            padding: 0 16px;
            min-height: 82px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            margin-bottom: 14px;
        }

        /* ── section title ────────────────────────────── */
        .section-title {
            font-size: 0.72rem; font-weight: 700;
            color: #6c757d;
            letter-spacing: 0.9px;
            text-transform: uppercase;
            margin: 1rem 0 0.5rem;
        }

        /* ── process card (sidebar result) ───────────── */
        .proc-card {
            background: #FFFFFF;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            padding: 10px 14px 8px;
            margin-bottom: 4px;
        }
        .proc-card-title {
            font-size: 0.85rem; font-weight: 600;
            color: #1a3560; margin-bottom: 3px;
            line-height: 1.35;
        }
        .proc-card-sub {
            font-size: 0.74rem; color: #6c757d;
            line-height: 1.4;
        }
        .proc-card-badge {
            display: inline-block;
            font-size: 0.68rem; color: #6c757d;
            margin-top: 4px;
        }

        /* ── process header (main panel) ─────────────── */
        .proc-header {
            background: #FFFFFF;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            padding: 1.1rem 1.5rem;
            margin-bottom: 1rem;
        }
        .proc-header-title {
            font-size: 1.1rem; font-weight: 700;
            color: #1a3560; margin-bottom: 5px;
        }
        .proc-header-meta {
            font-size: 0.78rem; color: #6c757d;
            display: flex; gap: 14px; flex-wrap: wrap;
        }
        .proc-header-meta span { display: flex; align-items: center; gap: 4px; }

        /* ── metric cards ─────────────────────────────── */
        .metric-row { display: flex; gap: 12px; flex-wrap: wrap; margin: 1rem 0; }
        .metric-card {
            flex: 1; min-width: 130px;
            background: #FFFFFF;
            border-radius: 6px;
            padding: 16px 18px;
            border: 1px solid #dee2e6;
            border-top: 3px solid #00838f;
            text-align: left;
        }
        .metric-card.accent { border-top-color: #00838f; }
        .metric-value {
            font-size: 2rem; font-weight: 700;
            line-height: 1; margin-bottom: 4px; color: #212529;
        }
        .metric-card.accent .metric-value { color: #212529; }
        .metric-label {
            font-size: 0.65rem; font-weight: 600;
            letter-spacing: 0.8px; text-transform: uppercase; color: #6c757d;
        }
        .metric-sub {
            font-size: 0.72rem; color: #00838f;
            margin-top: 4px;
        }

        /* ── detail tags ──────────────────────────────── */
        .detail-tag {
            display: inline-block;
            background: #f0f4ff; border: 1px solid #c9d6f0;
            color: #1a3560; font-size: 0.72rem; font-weight: 500;
            padding: 2px 9px; border-radius: 4px; margin: 2px;
        }
        .detail-tag-systems { background: #e8f6f7; border-color: #a8d8dc; color: #00636b; }
        .detail-tag-kpi     { background: #fff4ee; border-color: #f5c6a0; color: #9b4a1a; }
        .detail-tag-oport   { background: #edf7ef; border-color: #a8d5b0; color: #1e5228; }

        /* ── accordion step card ──────────────────────── */
        .step-card {
            background: #FFFFFF;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 6px;
        }
        .step-title {
            font-size: 0.88rem; font-weight: 600; color: #212529;
            margin-bottom: 6px;
        }
        .step-desc {
            font-size: 0.82rem; color: #495057; line-height: 1.6;
            margin-bottom: 8px;
        }
        .level-badge {
            display: inline-block;
            font-size: 0.62rem; font-weight: 600;
            padding: 2px 8px; border-radius: 3px;
            letter-spacing: 0.4px; text-transform: uppercase;
            margin-right: 6px; margin-bottom: 4px;
            border: 1px solid;
        }
        .badge-n1 { background: #edf1f9; border-color: #b8c8e8; color: #1a3560; }
        .badge-n2 { background: #e8f0fb; border-color: #aec4f0; color: #1e4d9b; }
        .badge-n3 { background: #e8f6f7; border-color: #a8d8dc; color: #00636b; }
        .badge-n4 { background: #fff4ee; border-color: #f5c6a0; color: #9b4a1a; }

        /* ── tabs ─────────────────────────────────────── */
        .stTabs [data-baseweb="tab-list"] {
            background: transparent;
            border-bottom: 2px solid #dee2e6;
            padding: 0; gap: 0;
        }
        .stTabs [data-baseweb="tab"] {
            color: #6c757d; font-weight: 500;
            font-size: 0.85rem; border-radius: 0;
            padding: 8px 18px;
            background: transparent !important;
            border-bottom: 2px solid transparent;
            margin-bottom: -2px;
        }
        .stTabs [aria-selected="true"] {
            color: #1a3560 !important;
            border-bottom: 2px solid #1a3560 !important;
            font-weight: 600 !important;
        }
        .stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]) {
            color: #1a3560 !important;
            background: transparent !important;
        }

        /* ── buttons ──────────────────────────────────── */
        /* Buscar - primary */
        [data-testid="stSidebar"] button[kind="primary"],
        [data-testid="stSidebar"] [data-testid="stFormSubmitButton"] button {
            background: #1a3560 !important;
            color: #FFFFFF !important;
            border: none !important;
            border-radius: 5px !important;
            font-weight: 600 !important;
            font-size: 0.82rem !important;
        }
        [data-testid="stSidebar"] button[kind="primary"]:hover,
        [data-testid="stSidebar"] [data-testid="stFormSubmitButton"] button:hover {
            background: #162d56 !important;
        }
        /* Card buttons - secondary (sidebar results) */
        [data-testid="stSidebar"] button[kind="secondary"] {
            background: #FFFFFF !important;
            border: 1px solid #dee2e6 !important;
            border-radius: 6px !important;
            padding: 10px 14px 8px !important;
            text-align: left !important;
            width: 100% !important;
            height: auto !important;
            min-height: 64px !important;
            margin-bottom: 4px !important;
            display: flex !important;
            align-items: flex-start !important;
        }
        [data-testid="stSidebar"] button[kind="secondary"]:hover {
            border-color: #1a3560 !important;
            box-shadow: 0 2px 6px rgba(26,53,96,0.12) !important;
            background: #f0f4ff !important;
        }
        /* Primeira linha: título (negrito, azul escuro) */
        [data-testid="stSidebar"] button[kind="secondary"] p {
            white-space: pre-line !important;
            text-align: left !important;
            margin: 0 !important;
            font-size: 0.74rem !important;
            color: #6c757d !important;
            font-weight: 400 !important;
            line-height: 1.5 !important;
        }
        [data-testid="stSidebar"] button[kind="secondary"] p::first-line {
            font-size: 0.88rem !important;
            font-weight: 700 !important;
            color: #1a3560 !important;
        }
        /* Generic buttons fora do sidebar */
        .stButton>button {
            background: #FFFFFF;
            color: #495057;
            border: 1px solid #ced4da;
            border-radius: 5px;
            font-weight: 500;
            font-size: 0.82rem;
        }
        .stButton>button:hover {
            background: #f8f9fa;
            border-color: #adb5bd;
            color: #212529;
        }
        /* Metric btn estático (não-clicável) */
        .metric-btn-static {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            background: #FFFFFF;
            color: #495057;
            border: 1px solid #ced4da;
            border-radius: 5px;
            font-weight: 500;
            font-size: 0.82rem;
            padding: 0.45rem 1rem;
            box-sizing: border-box;
            min-height: 38px;
            user-select: none;
            margin: 0;
        }
        /* Remove margens do wrapper stMarkdownContainer ao redor do card estático */
        [data-testid="stMarkdownContainer"]:has(.metric-btn-static) {
            margin: 0 !important;
            padding: 0 !important;
            line-height: 0;
        }
        [data-testid="stMarkdownContainer"]:has(.metric-btn-static) .metric-btn-static {
            line-height: normal;
        }

        /* ── scrollbar ────────────────────────────────── */
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: #F8F9FA; }
        ::-webkit-scrollbar-thumb { background: #ced4da; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #adb5bd; }

        /* ── misc ─────────────────────────────────────── */
        hr { border-color: #dee2e6; }
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
        header { visibility: hidden; }
        /* O botão de reabrir a sidebar colapsada vive dentro do <header> —
           escondê-lo junto trava o usuário sem forma de reabrir a sidebar
           depois de recolhê-la. Reexibe só esse botão. */
        [data-testid="stExpandSidebarButton"] { visibility: visible !important; }

        .empty-state {
            text-align: center;
            padding: 3rem 2rem;
            color: #6c757d;
        }
        .empty-state-icon  { font-size: 3rem; margin-bottom: 1rem; }
        .empty-state-title { font-size: 1.1rem; font-weight: 600; color: #1a3560; margin-bottom: 0.5rem; }
        .empty-state-text  { font-size: 0.85rem; }

        /* ── metric detail panel ──────────────────────── */
        .metric-detail-panel {
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 1rem;
        }
        .metric-detail-title {
            font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.8px; color: #6c757d; margin-bottom: 8px;
        }
        .metric-detail-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }

        /* ── Mermaid: contraste garantido via CSS de página ──────────────
           Reforça (não substitui) a diretiva %%{init}%% injetada em
           _apply_mermaid_theme(). Versões recentes do Streamlit calculam
           seus próprios themeVariables para mermaid.initialize() a partir
           da paleta do app, o que pode competir com a diretiva por-diagrama
           e deixar nós/texto escuros contra fundo escuro. Estas regras
           seguem os seletores padrão do mermaid.js e usam !important para
           garantir legibilidade independentemente de qual lado "ganha". */
        [data-testid="stMermaidChart"] {
            background: #f8f9fa !important;
            border-radius: 6px;
            padding: 8px;
        }
        [data-testid="stMermaidChart"] svg { background: transparent !important; }
        [data-testid="stMermaidChart"] .node rect,
        [data-testid="stMermaidChart"] .node polygon,
        [data-testid="stMermaidChart"] .node circle {
            fill: #7fb8ec !important;
            stroke: #2f6fb0 !important;
        }
        [data-testid="stMermaidChart"] .nodeLabel,
        [data-testid="stMermaidChart"] .node .label,
        [data-testid="stMermaidChart"] .node .label div,
        [data-testid="stMermaidChart"] .node text,
        [data-testid="stMermaidChart"] .node tspan {
            color: #0a1a2e !important;
            fill: #0a1a2e !important;
        }
        [data-testid="stMermaidChart"] .edgeLabel,
        [data-testid="stMermaidChart"] .edgeLabel rect {
            background-color: #f8f9fa !important;
            fill: #f8f9fa !important;
            color: #1a3560 !important;
        }
        [data-testid="stMermaidChart"] .edgeLabel text,
        [data-testid="stMermaidChart"] .edgeLabel tspan,
        [data-testid="stMermaidChart"] .edgeLabel span {
            color: #1a3560 !important;
            fill: #1a3560 !important;
        }
        [data-testid="stMermaidChart"] .edgePath .path,
        [data-testid="stMermaidChart"] .flowchart-link {
            stroke: #5a8fc7 !important;
            stroke-width: 2.2px !important;
        }
        [data-testid="stMermaidChart"] .arrowheadPath,
        [data-testid="stMermaidChart"] marker path {
            fill: #5a8fc7 !important;
            stroke: #5a8fc7 !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ─── Conexão Postgres (singleton via Cloud SQL Python Connector) ─────────────
@st.cache_resource(show_spinner=False)
def _get_connector():
    from google.cloud.sql.connector import Connector
    from google.oauth2 import service_account

    if _CREDENTIALS_PATH.exists():
        credentials = service_account.Credentials.from_service_account_file(
            str(_CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        return Connector(credentials=credentials)
    return Connector()


def _get_connection():
    connector = _get_connector()
    return connector.connect(
        _INSTANCE_CONNECTION_NAME,
        "pg8000",
        user=_DB_USER,
        password=_DB_PASSWORD,
        db=_DB_NAME,
    )


def _run_query(sql: str, params: tuple = ()) -> list[dict]:
    """Executa uma query no Postgres e retorna lista de dicts."""
    conn = _get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        columns = [c[0] for c in cur.description]
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()
        conn.close()


# ─── Queries de Busca ─────────────────────────────────────────────────────────
def search_n0_frentes(termo: str) -> list[dict]:
    """
    Busca n0_frente por correspondência parcial no campo nome.
    Retorna lista de dicts com: n0_id, frente_nome, macro_processos, total_tarefas.
    """
    busca = f"%{termo.strip()}%"

    sql = """
        SELECT
            f.id          AS n0_id,
            f.nome        AS frente_nome,
            STRING_AGG(DISTINCT mp.nome, ', ') AS macro_processos,
            COUNT(DISTINCT t.id)               AS total_tarefas
        FROM n0_frente f
        JOIN edge_has_n1 e1  ON f.id     = e1.n0_id
        JOIN n1_macroprocesso mp ON e1.n1_id = mp.id
        JOIN edge_has_n2 e2  ON mp.id    = e2.n1_id
        JOIN n2_processo p   ON e2.n2_id = p.id
        JOIN edge_has_n3 e3  ON p.id     = e3.n2_id
        JOIN n3_tarefa t     ON e3.n3_id = t.id
        WHERE f.nome ILIKE %s
        GROUP BY f.id, f.nome
        ORDER BY f.nome
    """
    return _run_query(sql, params=(busca,))


def load_process_detail(n0_id: str) -> dict:
    """
    Carrega a hierarquia completa N1→N2→N3→N4→N5 para um N0 selecionado.
    Retorna dict estruturado para renderização.
    """
    p = (n0_id,)

    # N0 nome
    n0_rows = _run_query(
        "SELECT id, nome FROM n0_frente WHERE id = %s",
        params=p,
    )
    frente_nome = n0_rows[0]["nome"] if n0_rows else n0_id

    # Hierarquia completa via JOINs
    sql = """
        SELECT
            mp.id    AS n1_id,   mp.nome  AS n1_nome,
            pr.id    AS n2_id,   pr.nome  AS n2_nome,
            ta.id    AS n3_id,   ta.nome  AS n3_nome,
            et.id    AS n4_id,   et.nome  AS n4_nome,
            atr.id    AS n5_id,
            atr.descricao,
            atr.entradas,
            atr.saidas,
            atr.sistemas_envolvidos,
            atr.kpis,
            atr.oportunidades_melhoria
        FROM n0_frente f
        JOIN edge_has_n1 e1  ON f.id     = e1.n0_id
        JOIN n1_macroprocesso mp ON e1.n1_id = mp.id
        JOIN edge_has_n2 e2  ON mp.id    = e2.n1_id
        JOIN n2_processo pr  ON e2.n2_id = pr.id
        JOIN edge_has_n3 e3  ON pr.id    = e3.n2_id
        JOIN n3_tarefa ta    ON e3.n3_id = ta.id
        JOIN edge_has_n4 e4  ON ta.id    = e4.n3_id
        JOIN n4_etapa et     ON e4.n4_id = et.id
        LEFT JOIN edge_has_n5 e5   ON et.id    = e5.n4_id
        LEFT JOIN n5_atributos atr ON e5.n5_id = atr.id
        WHERE f.id = %s
        ORDER BY mp.nome, pr.nome, ta.nome, et.nome
    """
    rows = _run_query(sql, params=p)

    # Métricas
    total_etapas    = len({r["n4_id"] for r in rows})
    total_tarefas   = len({r["n3_id"] for r in rows})
    total_macros    = len({r["n1_id"] for r in rows})
    total_processos = len({r["n2_id"] for r in rows})

    # Estrutura hierárquica
    hierarquia: dict[str, Any] = {}
    for r in rows:
        n1k = r["n1_id"]
        n2k = r["n2_id"]
        n3k = r["n3_id"]
        n4k = r["n4_id"]

        if n1k not in hierarquia:
            hierarquia[n1k] = {"nome": r["n1_nome"], "n2s": {}}

        n2s = hierarquia[n1k]["n2s"]
        if n2k not in n2s:
            n2s[n2k] = {"nome": r["n2_nome"], "n3s": {}}

        n3s = n2s[n2k]["n3s"]
        if n3k not in n3s:
            n3s[n3k] = {"nome": r["n3_nome"], "n4s": {}}

        n4s = n3s[n3k]["n4s"]
        if n4k not in n4s:
            n4s[n4k] = {
                "nome":                   r["n4_nome"],
                "descricao":              r.get("descricao") or "",
                "entradas":               r.get("entradas") or [],
                "saidas":                 r.get("saidas") or [],
                "sistemas_envolvidos":    r.get("sistemas_envolvidos") or [],
                "kpis":                   r.get("kpis") or [],
                "oportunidades_melhoria": r.get("oportunidades_melhoria") or [],
            }

    return {
        "n0_id":           n0_id,
        "frente_nome":     frente_nome,
        "total_etapas":    total_etapas,
        "total_tarefas":   total_tarefas,
        "total_macros":    total_macros,
        "total_processos": total_processos,
        "hierarquia":      hierarquia,
    }


def load_mermaid(n2_id: str) -> str | None:
    """Carrega o script Mermaid associado ao n2_processo selecionado."""
    rows = _run_query(
        "SELECT mermaid_script FROM n2_mermaid WHERE n2_id = %s",
        params=(n2_id,),
    )
    return rows[0]["mermaid_script"] if rows else None


def load_tobe(n2_id: str) -> str | None:
    """Carrega o documento Markdown TO-BE associado ao n2_processo selecionado."""
    rows = _run_query(
        "SELECT tobe_markdown FROM n2_tobe_documento WHERE n2_id = %s",
        params=(n2_id,),
    )
    return rows[0]["tobe_markdown"] if rows else None


_MERMAID_BRACKET_LABEL = re.compile(r'\[([^\[\]"]*[()][^\[\]"]*)\]')
_MERMAID_BRACE_LABEL = re.compile(r'\{([^{}"]*[()][^{}"]*)\}')


def _quote_mermaid_labels(mermaid_code: str) -> str:
    """Envolve em aspas labels de nós ([...] e {...}) que contêm parênteses.

    O parser do mermaid.js trata '(' e ')' dentro de um label sem aspas como
    início de outro formato de nó (ex: "Geração AS-IS (LLM)" quebra com
    "Parse error... got 'PS'"). Corrige diagramas já persistidos no Postgres
    antes de gerar essa correção (a extração em generate_artifacts.py já
    aplica o mesmo tratamento para diagramas novos).
    """
    if not mermaid_code:
        return mermaid_code
    code = _MERMAID_BRACKET_LABEL.sub(lambda m: f'["{m.group(1)}"]', mermaid_code)
    code = _MERMAID_BRACE_LABEL.sub(lambda m: f'{{"{m.group(1)}"}}', code)
    return code


_MERMAID_INIT_DIRECTIVE = (
    "%%{init: {"
    "'theme': 'base',"
    "'themeVariables': {"
    "'primaryColor': '#7fb8ec',"
    "'primaryTextColor': '#0a1a2e',"
    "'primaryBorderColor': '#2f6fb0',"
    "'lineColor': '#7fb8ec',"
    "'secondaryColor': '#a9d0f0',"
    "'tertiaryColor': '#0f1b30',"
    "'fontSize': '15px'"
    "},"
    "'themeCSS': "
    "'.edgePath .path, .flowchart-link "
    "{ stroke: #7fb8ec !important; stroke-width: 2.2px !important; } "
    ".arrowheadPath, marker path "
    "{ fill: #7fb8ec !important; stroke: #7fb8ec !important; } "
    ".edgeLabel "
    "{ background-color: #0f1b30 !important; color: #f4f6fb !important; } "
    ".node rect, .node polygon, .node circle "
    "{ fill: #7fb8ec !important; stroke: #2f6fb0 !important; } "
    ".node .label, .node .label div, .nodeLabel "
    "{ color: #0a1a2e !important; }'"
    "}}%%"
)


def _apply_mermaid_theme(mermaid_code: str) -> str:
    """Prefixa o diagrama com um tema Mermaid de alto contraste.

    O tema padrão do mermaid.js renderiza as linhas de conexão em cinza claro
    e finas — quase invisíveis contra o fundo escuro do Streamlit. Injeta uma
    diretiva ``%%{init: ...}%%`` (deve ser a primeira linha do diagrama) para
    engrossar/colorir as linhas e setas sem alterar o texto/estrutura do grafo.
    """
    if not mermaid_code or mermaid_code.lstrip().startswith("%%{init"):
        return mermaid_code
    return f"{_MERMAID_INIT_DIRECTIVE}\n{mermaid_code}"


# ─── Helpers de renderização ──────────────────────────────────────────────────
def _tags_html(items: list, css_class: str = "detail-tag") -> str:
    if not items:
        return '<span style="color:#6c757d;font-size:0.8rem;font-style:italic;">Não informado</span>'
    safe_class = html.escape(css_class, quote=True)
    return "".join(f'<span class="{safe_class}">{html.escape(str(item))}</span>' for item in items)


def _esc(value: Any) -> str:
    """Escapa qualquer valor para uso seguro dentro de HTML (previne XSS).

    Necessário porque nomes/descrições vêm do JSON extraído por LLM a partir
    de documentos enviados por usuários — texto não confiável que pode
    conter marcações HTML/JS maliciosas.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _metric_html(emoji: str, value: int, label: str, accent: bool = False) -> str:
    cls = "metric-card accent" if accent else "metric-card"
    return (
        f'<div class="{cls}">'
        f'<div style="font-size:1.1rem;margin-bottom:2px;">{emoji}</div>'
        f'<div class="metric-value">{value}</div>'
        f'<div class="metric-label">{label}</div>'
        f'</div>'
    )


# ─── Renderização do painel principal ────────────────────────────────────────
def render_process_detail(detail: dict) -> None:
    """Renderiza o painel principal com os detalhes do processo selecionado."""

    # Coleta nomes dos N1 e contagem de sistemas para o banner
    n1_nomes = [n1["nome"] for n1 in detail["hierarquia"].values()]
    n1_txt   = ", ".join(_esc(n) for n in n1_nomes[:3])
    if len(n1_nomes) > 3:
        n1_txt += f" +{len(n1_nomes) - 3} mais"

    all_sistemas: set = set()
    for _n1 in detail["hierarquia"].values():
        for _n2 in _n1["n2s"].values():
            for _n3 in _n2["n3s"].values():
                for _n4 in _n3["n4s"].values():
                    for _s in (_n4.get("sistemas_envolvidos") or []):
                        if _s:
                            all_sistemas.add(_s)
    total_sistemas = len(all_sistemas)

    meta_parts = []
    if n1_txt:
        meta_parts.append(n1_txt)
    meta_parts.append(f"📋 {detail['total_tarefas']} tarefas")
    if total_sistemas > 0:
        meta_parts.append(f"🖥️ {total_sistemas} sistemas")
    meta_line2 = "  ·  ".join(meta_parts)

    # Header do processo
    st.markdown(
        f"""
        <div class="proc-header">
            <div class="proc-header-title">🏗️ {_esc(detail["frente_nome"])}</div>
            <div class="proc-header-meta">
                <span>{meta_line2}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Abas
    tab_passo, tab_fluxo, tab_tobe = st.tabs(
        ["📋  Passo a Passo", "🔀  Diagrama de Fluxo", "📝  Proposta TO-BE"]
    )

    # Se o usuário clicou num card da sidebar enquanto estava na aba Diagrama,
    # injeta JS que clica na primeira aba (Passo a Passo) e consome o flag.
    if st.session_state.pop("switch_to_passo_tab", False):
        components.html(
            """<script>
            setTimeout(function() {
                try {
                    // Tenta seletores em ordem de especificidade
                    var tabs = window.parent.document.querySelectorAll('button[data-baseweb="tab"]');
                    if (!tabs || tabs.length === 0)
                        tabs = window.parent.document.querySelectorAll('[data-testid="stTab"]');
                    if (!tabs || tabs.length === 0)
                        tabs = window.parent.document.querySelectorAll('[role="tab"]');
                    if (tabs && tabs.length > 0) tabs[0].click();
                } catch(e) {}
            }, 300);
            </script>""",
            height=1,
        )

    # ── ABA: Passo a Passo ────────────────────────────────────────────────────
    with tab_passo:
        hierarquia = detail["hierarquia"]

        if not hierarquia:
            st.info("Nenhuma etapa encontrada para este processo.")
        else:
            for n1_id, n1_data in hierarquia.items():
                with st.expander(f"⚙️  **{n1_data['nome']}**", expanded=False):
                    for n2_id, n2_data in n1_data["n2s"].items():
                        # N2
                        st.markdown(
                            f'<div style="margin:8px 0 6px;">'
                            f'<span class="level-badge badge-n2">Processo</span>'
                            f'<span style="font-size:0.88rem;font-weight:700;color:#1E5BB0;">'
                            f'{_esc(n2_data["nome"])}</span></div>',
                            unsafe_allow_html=True,
                        )

                        for n3_id, n3_data in n2_data["n3s"].items():
                            # N3
                            st.markdown(
                                f'<div style="margin:6px 0 4px 12px;">'
                                f'<span class="level-badge badge-n3">Tarefa</span>'
                                f'<span style="font-size:0.84rem;font-weight:600;color:#006D75;">'
                                f'{_esc(n3_data["nome"])}</span></div>',
                                unsafe_allow_html=True,
                            )

                            for n4_id, n4_data in n3_data["n4s"].items():
                                descricao = _esc(n4_data.get("descricao") or "Não informado")
                                entradas  = _tags_html(n4_data.get("entradas") or [])
                                saidas    = _tags_html(n4_data.get("saidas") or [])
                                sistemas  = _tags_html(
                                    n4_data.get("sistemas_envolvidos") or [],
                                    "detail-tag detail-tag-systems",
                                )
                                kpis   = _tags_html(
                                    n4_data.get("kpis") or [],
                                    "detail-tag detail-tag-kpi",
                                )
                                oports = _tags_html(
                                    n4_data.get("oportunidades_melhoria") or [],
                                    "detail-tag detail-tag-oport",
                                )

                                st.markdown(
                                    f"""
                                    <div class="step-card" style="margin-left:24px;">
                                        <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                                            <span class="level-badge badge-n4">Etapa</span>
                                            <span class="step-title" style="margin:0;">{_esc(n4_data["nome"])}</span>
                                        </div>
                                        <div style="margin-bottom:10px;">
                                            <div style="font-size:0.65rem;color:#6c757d;font-weight:600;
                                                        letter-spacing:0.8px;text-transform:uppercase;margin-bottom:4px;">
                                                Descrição
                                            </div>
                                            <div class="step-desc">{descricao}</div>
                                        </div>
                                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:8px;">
                                            <div>
                                                <div style="font-size:0.62rem;color:#6c757d;font-weight:600;
                                                            text-transform:uppercase;margin-bottom:3px;">Entradas</div>
                                                <div>{entradas}</div>
                                            </div>
                                            <div>
                                                <div style="font-size:0.62rem;color:#6c757d;font-weight:600;
                                                            text-transform:uppercase;margin-bottom:3px;">Saídas</div>
                                                <div>{saidas}</div>
                                            </div>
                                        </div>
                                        <div style="margin-bottom:6px;">
                                            <div style="font-size:0.62rem;color:#6c757d;font-weight:600;
                                                        text-transform:uppercase;margin-bottom:3px;">Sistemas Envolvidos</div>
                                            <div>{sistemas}</div>
                                        </div>
                                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">
                                            <div>
                                                <div style="font-size:0.62rem;color:#6c757d;font-weight:600;
                                                            text-transform:uppercase;margin-bottom:3px;">KPIs</div>
                                                <div>{kpis}</div>
                                            </div>
                                            <div>
                                                <div style="font-size:0.62rem;color:#6c757d;font-weight:600;
                                                            text-transform:uppercase;margin-bottom:3px;">Oportunidades de Melhoria</div>
                                                <div>{oports}</div>
                                            </div>
                                        </div>
                                    </div>
                                    """,
                                    unsafe_allow_html=True,
                                )
                        st.markdown(
                            "<hr style='margin:6px 0;border-color:#dee2e6;'>",
                            unsafe_allow_html=True,
                        )

    # ── ABA: Diagrama de Fluxo ────────────────────────────────────────────────
    with tab_fluxo:
        st.markdown(
            '<div class="section-title">Diagrama de Fluxo por Processo (N2)</div>',
            unsafe_allow_html=True,
        )

        hierarquia = detail["hierarquia"]
        found_any = False

        for n1_data in hierarquia.values():
            for n2_id, n2_data in n1_data["n2s"].items():
                with st.spinner(f"Carregando diagrama de '{n2_data['nome']}'…"):
                    mermaid_script = load_mermaid(n2_id)

                if mermaid_script:
                    found_any = True
                    st.markdown(
                        f'<div style="font-size:0.82rem;font-weight:700;color:#1a3560;'
                        f'margin:12px 0 4px;">'
                        f'<span class="level-badge badge-n2">Processo</span>'
                        f'{_esc(n2_data["nome"])}</div>',
                        unsafe_allow_html=True,
                    )
                    script_clean = mermaid_script.strip()
                    if script_clean.startswith("```"):
                        script_clean = script_clean.strip("`").removeprefix("mermaid").strip()
                    script_clean = _apply_mermaid_theme(_quote_mermaid_labels(script_clean))
                    st.mermaid_chart(script_clean)

        if not found_any:
            st.info(
                "Nenhum diagrama Mermaid encontrado. "
                "Execute o agente para gerar os diagramas.",
                icon="ℹ️",
            )

    # ── ABA: Proposta TO-BE ────────────────────────────────────────────────────
    with tab_tobe:
        st.markdown(
            '<div class="section-title">Proposta de Processo Futuro (TO-BE) por Processo (N2)</div>',
            unsafe_allow_html=True,
        )

        hierarquia = detail["hierarquia"]
        found_any_tobe = False

        for n1_data in hierarquia.values():
            for n2_id, n2_data in n1_data["n2s"].items():
                with st.spinner(f"Carregando proposta TO-BE de '{n2_data['nome']}'…"):
                    tobe_markdown = load_tobe(n2_id)

                if tobe_markdown:
                    found_any_tobe = True
                    st.markdown(
                        f'<div style="font-size:0.82rem;font-weight:700;color:#1a3560;'
                        f'margin:12px 0 4px;">'
                        f'<span class="level-badge badge-n2">Processo</span>'
                        f'{_esc(n2_data["nome"])}</div>',
                        unsafe_allow_html=True,
                    )
                    with st.container(border=True):
                        # unsafe_allow_html=False (padrão): renderiza o Markdown
                        # gerado pelo LLM como texto/markdown puro, sem interpretar
                        # tags HTML embutidas — mesma cautela do restante da página
                        # com conteúdo vindo de documentos de usuário.
                        st.markdown(tobe_markdown)

        if not found_any_tobe:
            st.info(
                "Nenhuma proposta TO-BE encontrada. "
                "Execute o agente para gerar a proposta.",
                icon="ℹ️",
            )

    # ── Resumo do Processo ────────────────────────────────────────────────────
    st.markdown(
        '<div class="section-title" style="margin-top:1.5rem;">Resumo do Processo</div>',
        unsafe_allow_html=True,
    )

    # Extrair listas de nomes por nível
    hie = detail["hierarquia"]
    n1_nomes = sorted({n1["nome"] for n1 in hie.values()})
    n2_nomes = sorted({n2["nome"] for n1 in hie.values() for n2 in n1["n2s"].values()})
    n3_nomes = sorted({n3["nome"] for n1 in hie.values() for n2 in n1["n2s"].values() for n3 in n2["n3s"].values()})
    n4_nomes = sorted({n4["nome"] for n1 in hie.values() for n2 in n1["n2s"].values() for n3 in n2["n3s"].values() for n4 in n3["n4s"].values()})

    metrics_def = [
        ("m_macros",    "⚙️",  detail["total_macros"],    "Macro Processos", n1_nomes,  True),
        ("m_processos", "🔄",  detail["total_processos"], "Processos",       n2_nomes,  True),
        ("m_tarefas",   "📋",  detail["total_tarefas"],   "Tarefas",         n3_nomes,  True),
        ("m_etapas",    "▶️", detail["total_etapas"],    "Total de Etapas", n4_nomes,  False),
    ]

    if "active_metric" not in st.session_state:
        st.session_state["active_metric"] = None

    cols = st.columns(4, vertical_alignment="center")
    for col, (key, emoji, value, label, _, clickable) in zip(cols, metrics_def):
        with col:
            if clickable:
                st.markdown('<div class="metric-btn">', unsafe_allow_html=True)
                if st.button(
                    f"{value}\n{emoji}  {label}",
                    key=f"metric_{key}_{detail['n0_id']}",
                    use_container_width=True,
                ):
                    st.session_state["active_metric"] = (
                        None if st.session_state["active_metric"] == key else key
                    )
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.markdown(
                    f'<div class="metric-btn-static">{value}&nbsp;{emoji}&nbsp;&nbsp;{label}</div>',
                    unsafe_allow_html=True,
                )

    # Painel expansivo abaixo dos cards
    active = st.session_state.get("active_metric")
    if active:
        match = next((m for m in metrics_def if m[0] == active), None)
        if match:
            _, _, _, label, nomes, _ = match
            tags = "".join(
                f'<span class="detail-tag" style="margin:2px 3px;">{_esc(n)}</span>'
                for n in nomes
            )
            st.markdown(
                f'<div class="metric-detail-panel">'
                f'<div class="metric-detail-title">{_esc(label)} ({len(nomes)})</div>'
                f'<div class="metric-detail-tags">{tags}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main() -> None:
    st.set_page_config(
        page_title="Mapa de Processos AS-IS",
        page_icon="🧠",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _inject_css()

    # Inicializa session state
    if "search_results" not in st.session_state:
        st.session_state["search_results"] = []
    if "selected_n0_id" not in st.session_state:
        st.session_state["selected_n0_id"] = None
    if "selected_n0_nome" not in st.session_state:
        st.session_state["selected_n0_nome"] = None
    if "process_detail" not in st.session_state:
        st.session_state["process_detail"] = None
    if "switch_to_passo_tab" not in st.session_state:
        st.session_state["switch_to_passo_tab"] = False

    # Hero Header
    st.markdown(
        """
        <div class="hero">
            <div class="hero-title">Mapa de Processos AS-IS</div>
            <div class="hero-subtitle">
                Base de Conhecimento de Processos &nbsp;·&nbsp; Cloud SQL for PostgreSQL
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    st.sidebar.markdown(
        '<div class="sidebar-header">'
        '<div style="font-size:1rem;font-weight:700;color:#FFFFFF;">Processos AS-IS</div>'
        '<div style="font-size:0.75rem;color:#a8c0e0;margin-top:2px;">Base de Conhecimento</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.sidebar.markdown(
        '<div class="section-title">🔍 Pesquisar Processos</div>',
        unsafe_allow_html=True,
    )
    with st.sidebar.form(key="search_form", border=False):
        termo = st.text_input(
            "Buscar por nome",
            placeholder="Digite parte do nome…",
            label_visibility="collapsed",
        )
        buscar_clicked = st.form_submit_button("Buscar", use_container_width=True, type="primary")

    # Executa busca ao clicar ou pressionar Enter
    if buscar_clicked:
        if not termo.strip():
            st.sidebar.warning("Digite ao menos um caractere para buscar.")
        else:
            with st.spinner("Buscando…"):
                try:
                    resultados = search_n0_frentes(termo)
                    st.session_state["search_results"]  = resultados
                    st.session_state["selected_n0_id"]  = None
                    st.session_state["selected_n0_nome"] = None
                    st.session_state["process_detail"]  = None
                    if not resultados:
                        st.sidebar.info("Nenhum processo encontrado.")
                except Exception:
                    logger.exception("Erro na busca por termo=%r", termo)
                    st.sidebar.error(
                        "Não foi possível concluir a busca. Tente novamente em instantes."
                    )
                    st.session_state["search_results"] = []

    # Lista de resultados
    resultados = st.session_state.get("search_results", [])
    if resultados:
        st.sidebar.markdown(
            f'<div style="font-size:0.72rem;color:#6c757d;margin:8px 0 4px;">'
            f'{len(resultados)} resultado(s) encontrado(s)</div>',
            unsafe_allow_html=True,
        )

        for idx, res in enumerate(resultados):
            macro_txt   = res.get("macro_processos") or "—"
            total_taref = res.get("total_tarefas") or 0

            label = f"{res['frente_nome']}\n{macro_txt}  ·  📋 {total_taref} tarefas"

            if st.sidebar.button(
                label,
                key=f"card_{res['n0_id']}_{idx}",
                use_container_width=True,
            ):
                with st.spinner(f"Carregando {res['frente_nome']}…"):
                    try:
                        detail = load_process_detail(res["n0_id"])
                        st.session_state["selected_n0_id"]       = res["n0_id"]
                        st.session_state["selected_n0_nome"]     = res["frente_nome"]
                        st.session_state["process_detail"]       = detail
                        st.session_state["active_metric"]        = None
                        st.session_state["switch_to_passo_tab"] = True
                        st.rerun()
                    except Exception:
                        logger.exception(
                            "Erro ao carregar processo n0_id=%r", res["n0_id"]
                        )
                        st.sidebar.error(
                            "Não foi possível carregar este processo. Tente novamente em instantes."
                        )

    # ── PAINEL PRINCIPAL ──────────────────────────────────────────────────────
    detail = st.session_state.get("process_detail")

    if detail:
        render_process_detail(detail)
    else:
        st.markdown(
            """
            <div class="empty-state">
                <div class="empty-state-icon">🏛️</div>
                <div class="empty-state-title">Nenhum processo selecionado</div>
                <div class="empty-state-text">
                    Use a caixa de pesquisa na barra lateral para buscar um processo<br>
                    e clique no resultado para visualizar os detalhes.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


if __name__ == "__main__":
    main()
