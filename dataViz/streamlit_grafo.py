"""
╔══════════════════════════════════════════════════════════════════════════════╗
║     MAPA DE PROCESSOS AS-IS  ·  Visualizador de Processos                   ║
║     Google Cloud Spanner (db-agente-processo) · Streamlit                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

Pesquisa processos na tabela N0_Frente do Spanner e exibe a hierarquia
completa N0 → N1 → N2 → N3 → N4 → N5 com Mermaid para o diagrama de fluxo.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components

# ─── Desabilita métricas internas do Spanner (evita travamento sem roles) ────
os.environ.setdefault("SPANNER_ENABLE_BUILTIN_METRICS", "false")

# ─── Import do normalizador Mermaid (mesmo usado no pipeline PDF) ────────────
try:
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agente_gerador_pdf_md.tools.markdown_to_pdf_tool import _clean_mermaid_code as _normalise_mermaid
except Exception as _e:
    import sys as _sys
    print(f"[WARN] _clean_mermaid_code não importado: {_e} — usando fallback básico", file=_sys.stderr)
    import re as _re
    def _normalise_mermaid(s: str) -> str:  # type: ignore[misc]
        """Fallback: colapsa quebras dentro de labels."""
        s = s.replace('\r\n', '\n').replace('\r', '\n')
        s = _re.sub(r'(\[[^\]]*?)\n([^\]]*?\])', lambda m: m.group(0).replace('\n', ' '), s)
        s = _re.sub(r'(\{[^}]*?)\n([^}]*?\})', lambda m: m.group(0).replace('\n', ' '), s)
        return s

# ─── Caminhos / configurações ────────────────────────────────────────────────
_ROOT             = Path(__file__).resolve().parent
_PROJECT_ROOT     = _ROOT.parent          # raiz do projeto (um nível acima de dataViz/)
_CREDENTIALS_PATH = _PROJECT_ROOT / "credentials.json"

_GCP_PROJECT      = "steady-computer-487217-p6"
_SPANNER_INSTANCE = "id-agente-processo"
_SPANNER_DATABASE = "db-agente-processo"


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
        </style>
        """,
        unsafe_allow_html=True,
    )


# ─── Conexão Spanner (singleton) ─────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def _get_spanner_database():
    from google.cloud import spanner
    from google.oauth2 import service_account

    if _CREDENTIALS_PATH.exists():
        credentials = service_account.Credentials.from_service_account_file(
            str(_CREDENTIALS_PATH),
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        client = spanner.Client(project=_GCP_PROJECT, credentials=credentials)
    else:
        client = spanner.Client(project=_GCP_PROJECT)

    return client.instance(_SPANNER_INSTANCE).database(_SPANNER_DATABASE)


def _run_query(sql: str, params: dict | None = None, types: dict | None = None) -> list[dict]:
    """Executa uma query no Spanner e retorna lista de dicts.

    Nota: o SDK do Spanner só popula results.fields (metadados) após a iteração
    completa do StreamedResultSet. Por isso consumimos todas as linhas com list()
    antes de acessar fields, evitando o erro 'NoneType has no attribute row_type'.
    """
    db = _get_spanner_database()
    with db.snapshot() as snap:
        results = snap.execute_sql(sql, params=params or {}, param_types=types or {})
        rows = list(results)          # consome o stream → popula results.fields
        fields = [f.name for f in results.fields]
        return [dict(zip(fields, row)) for row in rows]


# ─── Queries de Busca ─────────────────────────────────────────────────────────
def search_n0_frentes(termo: str) -> list[dict]:
    """
    Busca N0_Frente por correspondência parcial no campo nome.
    Retorna lista de dicts com: n0_id, frente_nome, macro_processos, total_tarefas.
    """
    from google.cloud.spanner_v1 import param_types

    busca = f"%{termo.strip()}%"

    sql = """
        SELECT
            f.id          AS n0_id,
            f.nome        AS frente_nome,
            STRING_AGG(DISTINCT mp.nome, ', ') AS macro_processos,
            COUNT(DISTINCT t.id)               AS total_tarefas
        FROM N0_Frente f
        JOIN Edge_Has_N1 e1  ON f.id     = e1.n0_id
        JOIN N1_MacroProcesso mp ON e1.n1_id = mp.id
        JOIN Edge_Has_N2 e2  ON mp.id    = e2.n1_id
        JOIN N2_Processo p   ON e2.n2_id = p.id
        JOIN Edge_Has_N3 e3  ON p.id     = e3.n2_id
        JOIN N3_Tarefa t     ON e3.n3_id = t.id
        WHERE LOWER(f.nome) LIKE LOWER(@busca)
        GROUP BY f.id, f.nome
        ORDER BY f.nome
    """
    return _run_query(
        sql,
        params={"busca": busca},
        types={"busca": param_types.STRING},
    )


def load_process_detail(n0_id: str) -> dict:
    """
    Carrega a hierarquia completa N1→N2→N3→N4→N5 para um N0 selecionado.
    Retorna dict estruturado para renderização.
    """
    from google.cloud.spanner_v1 import param_types

    p = {"n0_id": n0_id}
    t = {"n0_id": param_types.STRING}

    # N0 nome
    n0_rows = _run_query(
        "SELECT id, nome FROM N0_Frente WHERE id = @n0_id",
        params=p, types=t,
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
        FROM N0_Frente f
        JOIN Edge_Has_N1 e1  ON f.id     = e1.n0_id
        JOIN N1_MacroProcesso mp ON e1.n1_id = mp.id
        JOIN Edge_Has_N2 e2  ON mp.id    = e2.n1_id
        JOIN N2_Processo pr  ON e2.n2_id = pr.id
        JOIN Edge_Has_N3 e3  ON pr.id    = e3.n2_id
        JOIN N3_Tarefa ta    ON e3.n3_id = ta.id
        JOIN Edge_Has_N4 e4  ON ta.id    = e4.n3_id
        JOIN N4_Etapa et     ON e4.n4_id = et.id
        LEFT JOIN Edge_Has_N5 e5   ON et.id    = e5.n4_id
        LEFT JOIN N5_Atributos atr ON e5.n5_id = atr.id
        WHERE f.id = @n0_id
        ORDER BY mp.nome, pr.nome, ta.nome, et.nome
    """
    rows = _run_query(sql, params=p, types=t)

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
    """Carrega o script Mermaid associado ao N2_Processo selecionado."""
    from google.cloud.spanner_v1 import param_types

    rows = _run_query(
        "SELECT mermaid_script FROM N2_Mermaid WHERE n2_id = @n2_id",
        params={"n2_id": n2_id},
        types={"n2_id": param_types.STRING},
    )
    return rows[0]["mermaid_script"] if rows else None


# ─── Helpers de renderização ──────────────────────────────────────────────────
def _tags_html(items: list, css_class: str = "detail-tag") -> str:
    if not items:
        return '<span style="color:#6c757d;font-size:0.8rem;font-style:italic;">Não informado</span>'
    return "".join(f'<span class="{css_class}">{item}</span>' for item in items)


def _metric_html(emoji: str, value: int, label: str, accent: bool = False) -> str:
    cls = "metric-card accent" if accent else "metric-card"
    return (
        f'<div class="{cls}">'
        f'<div style="font-size:1.1rem;margin-bottom:2px;">{emoji}</div>'
        f'<div class="metric-value">{value}</div>'
        f'<div class="metric-label">{label}</div>'
        f'</div>'
    )


def _render_mermaid_html(script: str) -> str:
    """Gera HTML com mermaid.js CDN para renderização inline."""
    # Remove code fences se presentes
    script = script.strip()
    if script.startswith("```mermaid"):
        script = script[len("```mermaid"):].strip()
    if script.startswith("```"):
        script = script[3:].strip()
    if script.endswith("```"):
        script = script[:-3].strip()

    # Normaliza quebras de linha dentro de labels (causa de 'Syntax error in text')
    script = _normalise_mermaid(script)

    return f"""<!DOCTYPE html>
<html>
<head>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
    <style>
        body {{ background:#FFFFFF; margin:0; padding:16px; font-family:'Inter',sans-serif; }}
        .mermaid {{ display:flex; justify-content:center; }}
        svg {{ max-width:100%; }}
    </style>
</head>
<body>
    <div class="mermaid">
{script}
    </div>
    <script>
        mermaid.initialize({{
            startOnLoad: true,
            theme: 'base',
            themeVariables: {{
                primaryColor: '#1a3560',
                primaryTextColor: '#FFFFFF',
                primaryBorderColor: '#071A40',
                lineColor: '#1E5BB0',
                secondaryColor: '#EBF0F8',
                tertiaryColor: '#F4F5F7',
                fontSize: '14px'
            }}
        }});
    </script>
</body>
</html>"""

# ─── Renderização Mermaid ────────────────────────────────────────────────────
def _fetch_mermaid_image_bytes(script: str) -> bytes | None:
    """Renderiza o script Mermaid como PNG via mermaid.ink.

    Aplica _normalise_mermaid para corrigir quebras de linha dentro de labels,
    codifica em base64 e chama https://mermaid.ink/img/{base64}.
    Retorna bytes PNG ou None em caso de falha.
    """
    import base64
    import urllib.request

    s = script.strip()
    if s.startswith("```mermaid"):
        s = s[len("```mermaid"):].strip()
    if s.startswith("```"):
        s = s[3:].strip()
    if s.endswith("```"):
        s = s[:-3].strip()

    s = _normalise_mermaid(s)

    try:
        b64 = base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii")
        url = f"https://mermaid.ink/img/{b64}"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0", "Accept": "image/png,image/*,*/*"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        return data if len(data) > 100 else None
    except Exception as _e:
        import sys
        print(f"[Mermaid][ERROR] mermaid.ink falhou: {_e}", file=sys.stderr)
        return None

# ─── Renderização do painel principal ────────────────────────────────────────
def render_process_detail(detail: dict) -> None:
    """Renderiza o painel principal com os detalhes do processo selecionado."""

    # Coleta nomes dos N1 e contagem de sistemas para o banner
    n1_nomes = [n1["nome"] for n1 in detail["hierarquia"].values()]
    n1_txt   = ", ".join(n1_nomes[:3])
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
            <div class="proc-header-title">🏗️ {detail["frente_nome"]}</div>
            <div class="proc-header-meta">
                <span>{meta_line2}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Abas
    tab_passo, tab_fluxo = st.tabs(["📋  Passo a Passo", "🔀  Diagrama de Fluxo"])

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
                            f'{n2_data["nome"]}</span></div>',
                            unsafe_allow_html=True,
                        )

                        for n3_id, n3_data in n2_data["n3s"].items():
                            # N3
                            st.markdown(
                                f'<div style="margin:6px 0 4px 12px;">'
                                f'<span class="level-badge badge-n3">Tarefa</span>'
                                f'<span style="font-size:0.84rem;font-weight:600;color:#006D75;">'
                                f'{n3_data["nome"]}</span></div>',
                                unsafe_allow_html=True,
                            )

                            for n4_id, n4_data in n3_data["n4s"].items():
                                descricao = n4_data.get("descricao") or "Não informado"
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
                                            <span class="step-title" style="margin:0;">{n4_data["nome"]}</span>
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
                        f'{n2_data["nome"]}</div>',
                        unsafe_allow_html=True,
                    )
                    img_bytes = _fetch_mermaid_image_bytes(mermaid_script)
                    if img_bytes:
                        _, col_img, _ = st.columns([2, 2, 2])
                        with col_img:
                            st.image(
                                img_bytes,
                                use_container_width=True,
                            )
                    else:
                        # Fallback CDN caso mermaid.ink não responda
                        components.html(
                            _render_mermaid_html(mermaid_script),
                            height=600,
                            scrolling=True,
                        )

        if not found_any:
            st.info(
                "Nenhum diagrama Mermaid encontrado. "
                "Execute o agente para gerar os diagramas.",
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
                f'<span class="detail-tag" style="margin:2px 3px;">{n}</span>'
                for n in nomes
            )
            st.markdown(
                f'<div class="metric-detail-panel">'
                f'<div class="metric-detail-title">{label} ({len(nomes)})</div>'
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
                Base de Conhecimento de Processos &nbsp;·&nbsp; Google Cloud Spanner
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── SIDEBAR ───────────────────────────────────────────────────────────────
    st.sidebar.markdown(
        '<div class="sidebar-header">'
        '<div style="font-size:0.65rem;font-weight:700;letter-spacing:1px;'
        'text-transform:uppercase;color:#a8c0e0;margin-bottom:4px;">Cliente</div>'
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
                except Exception as exc:
                    st.sidebar.error(f"Erro na busca: {exc}")
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
                    except Exception as exc:
                        st.sidebar.error(f"Erro ao carregar processo: {exc}")

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
