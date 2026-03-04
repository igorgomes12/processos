"""
╔══════════════════════════════════════════════════════════════════════════════╗
║     MAPA DE PROCESSOS AS-IS  ·  Visualizador de Grafo Hierárquico           ║
║     Google Cloud Spanner (ProcessosGraph) · Streamlit + pyvis + Plotly      ║
╚══════════════════════════════════════════════════════════════════════════════╝

Lê os 6 tipos de nó e 5 tipos de aresta do grafo no Spanner:
  Nós   : N0_Frente → N1_MacroProcesso → N2_Processo → N3_Tarefa
                     → N4_Etapa → N5_Atributos
  Arestas: Edge_Has_N1, Edge_Has_N2, Edge_Has_N3, Edge_Has_N4, Edge_Has_N5

Fallback automático para debug_last_json.json se o Spanner não estiver
acessível (útil para desenvolvimento offline).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

# ─── Desabilita métricas internas do Spanner (evita travamento sem roles) ────
os.environ.setdefault("SPANNER_ENABLE_BUILTIN_METRICS", "false")

# ─── Caminhos / configurações ────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
_CREDENTIALS_PATH = _ROOT / "credentials.json"
_DEBUG_JSON = _ROOT / "debug_last_json.json"
_UUID_NAMESPACE = uuid.UUID("d3a7e1b0-9c4f-4e2a-8f1d-5b6c7e8f9a0b")

_GCP_PROJECT      = "steady-computer-487217-p6"
_SPANNER_INSTANCE = "id-agente-processo"
_SPANNER_DATABASE = "db-agente-processo"

# ─── Paleta corporativa BV (Cliente) ───────────────────────────────
# Azul marinho principal · Laranja BV como acento · Fundos claros
LEVEL_CONFIG: dict[str, dict[str, Any]] = {
    "N0": {
        "label":  "Frente",
        "color":  "#0C2D6B",   # azul marinho profundo (BV primário)
        "border": "#071A40",
        "font":   "#FFFFFF",
        "shape":  "star",
        "size":   52,
        "emoji":  "🏛️",
    },
    "N1": {
        "label":  "Macro Processo",
        "color":  "#1E5BB0",   # azul corporativo médio
        "border": "#0C2D6B",
        "font":   "#FFFFFF",
        "shape":  "hexagon",
        "size":   38,
        "emoji":  "⚙️",
    },
    "N2": {
        "label":  "Processo",
        "color":  "#006D75",   # azul-teal institucional
        "border": "#004D55",
        "font":   "#FFFFFF",
        "shape":  "ellipse",
        "size":   28,
        "emoji":  "🔄",
    },
    "N3": {
        "label":  "Tarefa",
        "color":  "#2D7A3A",   # verde corporativo
        "border": "#1E5228",
        "font":   "#FFFFFF",
        "shape":  "box",
        "size":   20,
        "emoji":  "📋",
    },
    "N4": {
        "label":  "Etapa",
        "color":  "#F26522",   # laranja BV (acento)
        "border": "#C4491A",
        "font":   "#FFFFFF",
        "shape":  "dot",
        "size":   14,
        "emoji":  "▶️",
    },
    "N5": {
        "label":  "Atributos",
        "color":  "#64748B",   # cinza ardósia (discreto)
        "border": "#475569",
        "font":   "#FFFFFF",
        "shape":  "diamond",
        "size":   11,
        "emoji":  "📝",
    },
}

EDGE_COLORS = {
    "N0-N1": "#4A7DBF",
    "N1-N2": "#2A9BAA",
    "N2-N3": "#5C9E6A",
    "N3-N4": "#F59454",
    "N4-N5": "#94A3B8",
}

# ─── CSS global ──────────────────────────────────────────────────────────────
def _inject_css() -> None:
    st.markdown(
        """
        <style>
        /* ── reset / base ─────────────────────────────────────────────
           Identidade corporativa BV (Cliente)
           Azul marinho #0C2D6B · Laranja #F26522 · Fundo #F4F5F7
        ──────────────────────────────────────────────────────────── */
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');

        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
            background-color: #F4F5F7;
        }

        /* ── fundo geral da página ───────────────────────────────────── */
        .stApp { background-color: #F4F5F7; }
        .main .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }

        /* ── header BV ───────────────────────────────────────────────── */
        .hero {
            background: #0C2D6B;
            border-radius: 8px;
            padding: 1.6rem 2rem;
            margin-bottom: 1.2rem;
            position: relative;
            overflow: hidden;
            border-left: 5px solid #F26522;
            box-shadow: 0 2px 12px rgba(12, 45, 107, 0.18);
        }
        .hero::after {
            content: "";
            position: absolute;
            right: 0; top: 0; bottom: 0;
            width: 180px;
            background: linear-gradient(90deg, transparent, rgba(242,101,34,0.12));
        }
        .hero-title {
            font-size: 1.65rem;
            font-weight: 800;
            color: #FFFFFF;
            letter-spacing: -0.3px;
            margin: 0;
        }
        .hero-subtitle {
            font-size: 0.88rem;
            color: #93B8E8;
            margin-top: 0.3rem;
            font-weight: 400;
        }
        .hero-badge {
            display: inline-block;
            background: rgba(242,101,34,0.18);
            border: 1px solid rgba(242,101,34,0.45);
            color: #FFB085;
            font-size: 0.7rem;
            font-weight: 700;
            padding: 2px 10px;
            border-radius: 4px;
            margin-top: 0.7rem;
            letter-spacing: 0.6px;
            text-transform: uppercase;
        }

        /* ── metric cards ────────────────────────────────────────────── */
        .metric-row { display: flex; gap: 10px; flex-wrap: wrap; margin-bottom: 1rem; }
        .metric-card {
            flex: 1; min-width: 100px;
            background: #FFFFFF;
            border-radius: 6px;
            padding: 12px 16px;
            border: 1px solid #E2E5EA;
            border-top: 3px solid #0C2D6B;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
            text-align: center;
            transition: box-shadow 0.2s, transform 0.15s;
        }
        .metric-card:hover {
            box-shadow: 0 4px 16px rgba(12,45,107,0.12);
            transform: translateY(-2px);
        }
        .metric-card.accent { border-top-color: #F26522; }
        .metric-value {
            font-size: 1.75rem;
            font-weight: 800;
            line-height: 1;
            margin-bottom: 3px;
            color: #0C2D6B;
        }
        .metric-card.accent .metric-value { color: #F26522; }
        .metric-label {
            font-size: 0.65rem;
            font-weight: 600;
            letter-spacing: 0.7px;
            text-transform: uppercase;
            color: #6B7280;
        }
        .metric-emoji { font-size: 1.1rem; margin-bottom: 3px; }

        /* ── section title ───────────────────────────────────────────── */
        .section-title {
            font-size: 0.95rem;
            font-weight: 700;
            color: #0C2D6B;
            border-left: 3px solid #F26522;
            padding-left: 10px;
            margin: 1rem 0 0.5rem;
        }

        /* ── legend ──────────────────────────────────────────────────── */
        .legend-item {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 4px 0;
            font-size: 0.8rem;
            color: #374151;
        }
        .legend-dot {
            width: 12px; height: 12px;
            border-radius: 2px;
            flex-shrink: 0;
        }

        /* ── detail card ─────────────────────────────────────────────── */
        .detail-card {
            background: #FFFFFF;
            border-radius: 8px;
            padding: 1.4rem 1.6rem;
            border: 1px solid #E2E5EA;
            border-top: 3px solid #F26522;
            box-shadow: 0 1px 6px rgba(0,0,0,0.06);
            margin-top: 0.8rem;
        }
        .detail-tag {
            display: inline-block;
            background: #EBF0F8;
            border: 1px solid #BDD0EB;
            color: #1E5BB0;
            font-size: 0.72rem;
            font-weight: 600;
            padding: 2px 9px;
            border-radius: 4px;
            margin: 2px;
        }
        .detail-tag-systems {
            background: #E6F4F5;
            border-color: #9DD1D5;
            color: #005D65;
        }
        .detail-tag-kpi {
            background: #FEF0E7;
            border-color: #F9B98A;
            color: #C4491A;
        }
        .detail-tag-oport {
            background: #EAF4EC;
            border-color: #9DCBA5;
            color: #1E5228;
        }

        /* ── tab override ────────────────────────────────────────────── */
        .stTabs [data-baseweb="tab-list"] {
            background: #FFFFFF;
            border-radius: 6px;
            padding: 3px 5px;
            border: 1px solid #E2E5EA;
            gap: 2px;
        }
        .stTabs [data-baseweb="tab"] {
            color: #6B7280;
            font-weight: 600;
            font-size: 0.82rem;
            border-radius: 4px;
            padding: 5px 14px;
        }
        .stTabs [aria-selected="true"] {
            background: #0C2D6B !important;
            color: #FFFFFF !important;
        }
        .stTabs [data-baseweb="tab"]:hover:not([aria-selected="true"]) {
            background: #EBF0F8 !important;
            color: #0C2D6B !important;
        }

        /* ── sidebar ─────────────────────────────────────────────────── */
        [data-testid="stSidebar"] {
            background: #FFFFFF;
            border-right: 1px solid #E2E5EA;
        }
        [data-testid="stSidebar"] .stMarkdown p,
        [data-testid="stSidebar"] label { color: #374151 !important; }
        [data-testid="stSidebar"] .section-title { color: #0C2D6B; }

        /* ── sidebar logo strip ──────────────────────────────────────── */
        .sidebar-header {
            background: #0C2D6B;
            border-radius: 6px;
            padding: 14px 16px;
            margin-bottom: 12px;
            border-left: 4px solid #F26522;
        }

        /* ── status badge ────────────────────────────────────────────── */
        .status-ok  { color: #2D7A3A; font-weight: 700; font-size: 0.78rem; }
        .status-warn { color: #C4491A; font-weight: 700; font-size: 0.78rem; }

        /* ── scrollbar ───────────────────────────────────────────────── */
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: #F4F5F7; }
        ::-webkit-scrollbar-thumb { background: #0C2D6B; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #F26522; }

        /* ── divider ─────────────────────────────────────────────────── */
        hr { border-color: #E2E5EA; }

        /* ── streamlit overrides ─────────────────────────────────────── */
        #MainMenu { visibility: hidden; }
        footer { visibility: hidden; }
        header { visibility: hidden; }
        .stButton>button {
            background: #0C2D6B;
            color: #FFFFFF;
            border: none;
            border-radius: 5px;
            font-weight: 600;
            font-size: 0.82rem;
        }
        .stButton>button:hover {
            background: #1E5BB0;
        }
        .stDownloadButton>button {
            background: #F26522;
            color: #FFFFFF;
            border: none;
            border-radius: 5px;
            font-weight: 600;
        }
        .stDownloadButton>button:hover { background: #C4491A; }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ─── Conexão Spanner (cache de recurso — reaproveita entre reruns) ────────────
@st.cache_resource(show_spinner=False)
def _get_spanner_database():
    """Singleton do Database Spanner usando as credenciais do projeto."""
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


# ─── Carga dos dados (com cache de 5 min) ─────────────────────────────────────
@st.cache_data(ttl=300, show_spinner=False)
def load_from_spanner() -> dict:
    """Lê todos os nós e arestas do Spanner e retorna um dicionário estruturado."""
    db = _get_spanner_database()

    def _query(sql: str) -> list[dict]:
        with db.snapshot() as snap:
            results = snap.execute_sql(sql)
            fields = [f.name for f in results.fields]
            return [dict(zip(fields, row)) for row in results]

    return {
        "N0": _query("SELECT id, nome FROM N0_Frente"),
        "N1": _query("SELECT id, nome FROM N1_MacroProcesso"),
        "N2": _query("SELECT id, nome FROM N2_Processo"),
        "N3": _query("SELECT id, nome FROM N3_Tarefa"),
        "N4": _query("SELECT id, nome FROM N4_Etapa"),
        "N5": _query(
            "SELECT id, descricao, entradas, saidas, "
            "sistemas_envolvidos, kpis, oportunidades_melhoria "
            "FROM N5_Atributos"
        ),
        "E01": _query("SELECT n0_id AS src, n1_id AS dst FROM Edge_Has_N1"),
        "E12": _query("SELECT n1_id AS src, n2_id AS dst FROM Edge_Has_N2"),
        "E23": _query("SELECT n2_id AS src, n3_id AS dst FROM Edge_Has_N3"),
        "E34": _query("SELECT n3_id AS src, n4_id AS dst FROM Edge_Has_N4"),
        "E45": _query("SELECT n4_id AS src, n5_id AS dst FROM Edge_Has_N5"),
    }


def _make_id_local(*parts: str) -> str:
    """Gera UUID v5 determinístico — mesma lógica do spanner_tool.py."""
    return str(uuid.uuid5(_UUID_NAMESPACE, "|".join(parts)))


@st.cache_data(ttl=300, show_spinner=False)
def load_from_debug_json() -> dict:
    """Constrói a mesma estrutura de dicionário a partir do debug_last_json.json."""
    if not _DEBUG_JSON.exists():
        return {}

    with open(_DEBUG_JSON, encoding="utf-8") as f:
        raw = json.load(f)

    rows: list[dict] = raw.get("rows", [])
    if not rows:
        return {}

    n0_map, n1_map, n2_map, n3_map, n4_map, n5_map = {}, {}, {}, {}, {}, {}
    edges01, edges12, edges23, edges34, edges45 = set(), set(), set(), set(), set()

    def _lst(v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x) for x in v if x]
        return [str(v)] if v else []

    for row in rows:
        v0 = (row.get("N0") or "").strip()
        v1 = (row.get("N1") or "").strip()
        v2 = (row.get("N2") or "").strip()
        v3 = (row.get("N3") or "").strip()
        v4 = (row.get("N4") or "").strip()

        id0 = _make_id_local(v0)
        id1 = _make_id_local(v0, v1)
        id2 = _make_id_local(v0, v1, v2)
        id3 = _make_id_local(v0, v1, v2, v3)
        id4 = _make_id_local(v0, v1, v2, v3, v4)
        id5 = _make_id_local(v0, v1, v2, v3, v4, "attrs")

        n0_map[id0] = {"id": id0, "nome": v0}
        n1_map[id1] = {"id": id1, "nome": v1}
        n2_map[id2] = {"id": id2, "nome": v2}
        n3_map[id3] = {"id": id3, "nome": v3}
        n4_map[id4] = {"id": id4, "nome": v4}
        n5_map[id5] = {
            "id": id5,
            "descricao": row.get("descricao") or "",
            "entradas": _lst(row.get("entradas")),
            "saidas": _lst(row.get("saidas")),
            "sistemas_envolvidos": _lst(row.get("sistemasEnvolvidos")),
            "kpis": _lst(row.get("kpis")),
            "oportunidades_melhoria": _lst(row.get("oportunidadesMelhoria")),
        }

        edges01.add((id0, id1))
        edges12.add((id1, id2))
        edges23.add((id2, id3))
        edges34.add((id3, id4))
        edges45.add((id4, id5))

    return {
        "N0": list(n0_map.values()),
        "N1": list(n1_map.values()),
        "N2": list(n2_map.values()),
        "N3": list(n3_map.values()),
        "N4": list(n4_map.values()),
        "N5": list(n5_map.values()),
        "E01": [{"src": s, "dst": d} for s, d in edges01],
        "E12": [{"src": s, "dst": d} for s, d in edges12],
        "E23": [{"src": s, "dst": d} for s, d in edges23],
        "E34": [{"src": s, "dst": d} for s, d in edges34],
        "E45": [{"src": s, "dst": d} for s, d in edges45],
    }


def load_data() -> tuple[dict, str]:
    """Tenta Spanner → fallback debug_last_json.json. Retorna (data, source)."""
    try:
        data = load_from_spanner()
        if data and data.get("N0"):
            return data, "spanner"
        raise ValueError("Spanner retornou dados vazios.")
    except Exception as exc:
        st.session_state["_spanner_error"] = str(exc)
        data = load_from_debug_json()
        return data, "debug_json"


# ─── Construção do grafo NetworkX ─────────────────────────────────────────────
def build_nx_graph(
    data: dict,
    show_n5: bool,
    filter_n0: list[str],
    filter_n1: list[str],
) -> nx.DiGraph:
    """Monta o grafo dirigido (DiGraph) com atributos de metadados em cada nó."""
    G = nx.DiGraph()

    id_to_level: dict[str, str] = {}

    # índices por id
    def _idx(records: list[dict]) -> dict[str, str]:
        return {r["id"]: r.get("nome", r.get("id", "?")) for r in records}

    idx = {
        "N0": _idx(data["N0"]),
        "N1": _idx(data["N1"]),
        "N2": _idx(data["N2"]),
        "N3": _idx(data["N3"]),
        "N4": _idx(data["N4"]),
    }

    # IDs permitidos pelos filtros
    allowed_n0: set[str] = set()
    for r in data["N0"]:
        if not filter_n0 or r["nome"] in filter_n0:
            allowed_n0.add(r["id"])

    allowed_n1: set[str] = set()
    for r in data["N1"]:
        if not filter_n1 or r["nome"] in filter_n1:
            allowed_n1.add(r["id"])

    # N1 conectados a N0 permitidos
    n1_from_n0: set[str] = {
        e["dst"] for e in data["E01"] if e["src"] in allowed_n0
    }
    allowed_n1 &= n1_from_n0 if filter_n1 else n1_from_n0

    # caminhos permitidos N2→N4 via N1
    allowed_n2: set[str] = {e["dst"] for e in data["E12"] if e["src"] in allowed_n1}
    allowed_n3: set[str] = {e["dst"] for e in data["E23"] if e["src"] in allowed_n2}
    allowed_n4: set[str] = {e["dst"] for e in data["E34"] if e["src"] in allowed_n3}

    # N5 index
    n5_idx: dict[str, dict] = {r["id"]: r for r in data.get("N5", [])}

    def _add_node(nid: str, level: str) -> None:
        if G.has_node(nid):
            return
        nome = idx.get(level, {}).get(nid, nid[:8] + "…")
        cfg = LEVEL_CONFIG[level]
        G.add_node(
            nid,
            label=nome,
            level=level,
            level_num=int(level[1]),
            color=cfg["color"],
            shape=cfg["shape"],
            size=cfg["size"],
            title=f"[{cfg['label']}]\n{nome}",
        )
        id_to_level[nid] = level

    # Adiciona nós e arestas apenas dentro do filtro
    for e in data["E01"]:
        s, d = e["src"], e["dst"]
        if s in allowed_n0 and d in allowed_n1:
            _add_node(s, "N0")
            _add_node(d, "N1")
            G.add_edge(s, d, color=EDGE_COLORS["N0-N1"], width=3)

    for e in data["E12"]:
        s, d = e["src"], e["dst"]
        if s in allowed_n1 and d in allowed_n2:
            _add_node(s, "N1")
            _add_node(d, "N2")
            G.add_edge(s, d, color=EDGE_COLORS["N1-N2"], width=2.5)

    for e in data["E23"]:
        s, d = e["src"], e["dst"]
        if s in allowed_n2 and d in allowed_n3:
            _add_node(s, "N2")
            _add_node(d, "N3")
            G.add_edge(s, d, color=EDGE_COLORS["N2-N3"], width=2)

    for e in data["E34"]:
        s, d = e["src"], e["dst"]
        if s in allowed_n3 and d in allowed_n4:
            _add_node(s, "N3")
            _add_node(d, "N4")
            G.add_edge(s, d, color=EDGE_COLORS["N3-N4"], width=1.5)

    if show_n5:
        for e in data["E45"]:
            s, d = e["src"], e["dst"]
            if s in allowed_n4:
                # N5 nó
                if G.has_node(s) and d in n5_idx:
                    rec = n5_idx[d]
                    desc = (rec.get("descricao") or "")[:120]
                    tooltip = (
                        f"[Atributos]\n"
                        f"Descrição: {desc}\n"
                        f"Entradas: {', '.join(rec.get('entradas', [])[:3])}\n"
                        f"Sistemas: {', '.join(rec.get('sistemas_envolvidos', [])[:3])}"
                    )
                    cfg = LEVEL_CONFIG["N5"]
                    G.add_node(
                        d,
                        label="📝",
                        level="N5",
                        level_num=5,
                        color=cfg["color"],
                        shape=cfg["shape"],
                        size=cfg["size"],
                        title=tooltip,
                    )
                    G.add_edge(s, d, color=EDGE_COLORS["N4-N5"], width=1)

    return G


# ─── Renderização pyvis ────────────────────────────────────────────────────────
def render_pyvis(
    G: nx.DiGraph,
    height: int,
    physics: bool,
    layout: str,
    node_labels: bool,
) -> str:
    """Gera o HTML completo do pyvis Network e retorna a string."""
    nt = Network(
        height=f"{height}px",
        width="100%",
        bgcolor="#FFFFFF",
        font_color="#1F2937",
        directed=True,
        notebook=False,
    )

    # Pré-calcula posições x,y para layouts hierárquicos.
    # Sem hierarquia vis.js os nós ficam livres para arrastar em qualquer direção.
    _positions: dict[str, tuple[float, float]] = {}
    if layout in ("Hierárquico (Top-Down)", "Hierárquico (Left-Right)"):
        from collections import defaultdict
        _levels: dict[int, list[str]] = defaultdict(list)
        for _nid, _attrs in G.nodes(data=True):
            _levels[_attrs["level_num"]].append(_nid)
        if layout == "Hierárquico (Top-Down)":
            _level_sep, _node_sp = 200, 200  # Y entre níveis, X entre nós do nível
            for _lvl, _nodes in _levels.items():
                _n = len(_nodes)
                for _i, _nid in enumerate(_nodes):
                    _positions[_nid] = ((_i - (_n - 1) / 2.0) * _node_sp, _lvl * _level_sep)
        else:  # Left-Right
            _level_sep, _node_sp = 280, 160  # X entre níveis, Y entre nós do nível
            for _lvl, _nodes in _levels.items():
                _n = len(_nodes)
                for _i, _nid in enumerate(_nodes):
                    _positions[_nid] = (_lvl * _level_sep, (_i - (_n - 1) / 2.0) * _node_sp)

    # Adiciona nós
    for nid, attrs in G.nodes(data=True):
        label = attrs["label"] if node_labels else ""
        nt.add_node(
            nid,
            label=label,
            title=attrs.get("title", attrs["label"]),
            color={
                "background": attrs["color"],
                "border": LEVEL_CONFIG[attrs["level"]]["border"],
                "highlight": {
                    "background": "#F59E0B",
                    "border": "#D97706",
                },
                "hover": {
                    "background": "#F59E0B",
                    "border": "#D97706",
                },
            },
            shape=attrs["shape"],
            size=attrs["size"],
            font={
                "color": LEVEL_CONFIG[attrs["level"]]["font"],
                "size": max(9, attrs["size"] // 3),
                "face": "Inter, sans-serif",
                "bold": attrs["level"] in ("N0", "N1"),
            },
            shadow={"enabled": True, "color": attrs["color"], "size": 12, "x": 0, "y": 4},
            level=attrs["level_num"],
            **({"x": _positions[nid][0], "y": _positions[nid][1]} if nid in _positions else {}),
        )

    # Adiciona arestas
    for src, dst, eattrs in G.edges(data=True):
        nt.add_edge(
            src, dst,
            color={"color": eattrs.get("color", "#6B7280"), "opacity": 0.75},
            width=eattrs.get("width", 1.5),
            arrows={"to": {"enabled": True, "scaleFactor": 0.6}},
            smooth={"type": "curvedCCW", "roundness": 0.15},
        )

    # Opções de física / layout
    if layout == "Hierárquico (Top-Down)":
        # Posições pré-calculadas acima; sem hierarchical engine para permitir arraste livre.
        nt.set_options("""
        {
            "layout": { "hierarchical": { "enabled": false } },
            "physics": { "enabled": false },
            "interaction": {
                "hover": true,
                "tooltipDelay": 100,
                "zoomView": true,
                "dragNodes": true,
                "dragView": true,
                "navigationButtons": true,
                "keyboard": { "enabled": true }
            }
        }
        """)
    elif layout == "Hierárquico (Left-Right)":
        # Posições pré-calculadas acima; sem hierarchical engine para permitir arraste livre.
        nt.set_options("""
        {
            "layout": { "hierarchical": { "enabled": false } },
            "physics": { "enabled": false },
            "interaction": {
                "hover": true,
                "tooltipDelay": 100,
                "zoomView": true,
                "dragNodes": true,
                "dragView": true,
                "navigationButtons": true,
                "keyboard": { "enabled": true }
            }
        }
        """)
    else:
        # ForceAtlas2 / Barnes-Hut
        opts = {
            "physics": {
                "enabled": physics,
                "stabilization": {"iterations": 200},
            },
            "layout": {"randomSeed": 42},
            "interaction": {
                "hover": True,
                "tooltipDelay": 150,
                "zoomView": True,
                "dragView": True,
                "navigationButtons": True,
                "keyboard": {"enabled": True},
            },
        }
        if physics:
            opts["physics"]["barnesHut"] = {
                "gravitationalConstant": -12000,
                "centralGravity": 0.3,
                "springLength": 180,
                "springConstant": 0.04,
                "damping": 0.09,
            }

        import json as _json
        nt.set_options(_json.dumps(opts))

    # Injeta estilos corporativos BV no HTML gerado
    html = nt.generate_html(name="graph.html", local=False)

    custom_style = """
    <style>
    body { background: #F4F5F7 !important; margin: 0; overflow: hidden; }
    #mynetwork {
        border: 1px solid #D1D9E6 !important;
        border-radius: 8px !important;
        background: #FFFFFF !important;
    }
    canvas { border-radius: 8px; }
    .vis-tooltip {
        background: #FFFFFF !important;
        border: 1px solid #0C2D6B !important;
        color: #1F2937 !important;
        font-family: 'Inter', sans-serif !important;
        font-size: 12px !important;
        border-radius: 6px !important;
        padding: 8px 12px !important;
        white-space: pre-line !important;
        max-width: 300px !important;
        box-shadow: 0 4px 16px rgba(12,45,107,0.15) !important;
    }
    .vis-navigation .vis-button {
        background-color: rgba(12,45,107,0.08) !important;
        border: 1px solid #BDD0EB !important;
        border-radius: 4px !important;
    }
    .vis-navigation .vis-button:hover {
        background-color: #0C2D6B !important;
    }
    </style>
    """
    html = html.replace("</head>", custom_style + "</head>")
    return html


# ─── Sunburst Plotly ──────────────────────────────────────────────────────────
def render_sunburst(data: dict, filter_n0: list[str], filter_n1: list[str]) -> go.Figure:
    """Gera sunburst mostrando N0→N1→N2→N3→N4."""
    ids_, labels_, parents_, values_, colors_ = [], [], [], [], []

    # Índices
    n0_names = {r["id"]: r["nome"] for r in data["N0"]}
    n1_names = {r["id"]: r["nome"] for r in data["N1"]}
    n2_names = {r["id"]: r["nome"] for r in data["N2"]}
    n3_names = {r["id"]: r["nome"] for r in data["N3"]}
    n4_names = {r["id"]: r["nome"] for r in data["N4"]}

    # Contagens
    n4_per_n3: dict[str, int] = {}
    for e in data["E34"]:
        n4_per_n3[e["src"]] = n4_per_n3.get(e["src"], 0) + 1

    n3_per_n2: dict[str, int] = {}
    for e in data["E23"]:
        n3_per_n2[e["src"]] = n3_per_n2.get(e["src"], 0) + n4_per_n3.get(e["dst"], 1)

    n2_per_n1: dict[str, int] = {}
    for e in data["E12"]:
        n2_per_n1[e["src"]] = n2_per_n1.get(e["src"], 0) + n3_per_n2.get(e["dst"], 1)

    n1_per_n0: dict[str, int] = {}
    for e in data["E01"]:
        n1_per_n0[e["src"]] = n1_per_n0.get(e["src"], 0) + n2_per_n1.get(e["dst"], 1)

    # Filtros
    allowed_n0 = set(
        r["id"] for r in data["N0"] if not filter_n0 or r["nome"] in filter_n0
    )
    n1_from_n0 = {e["dst"] for e in data["E01"] if e["src"] in allowed_n0}
    allowed_n1 = n1_from_n0.intersection(
        {r["id"] for r in data["N1"] if not filter_n1 or r["nome"] in filter_n1}
    )
    allowed_n2 = {e["dst"] for e in data["E12"] if e["src"] in allowed_n1}
    allowed_n3 = {e["dst"] for e in data["E23"] if e["src"] in allowed_n2}
    allowed_n4 = {e["dst"] for e in data["E34"] if e["src"] in allowed_n3}

    level_colors = {
        "root": "#0C2D6B",
        "N0":   "#0C2D6B",
        "N1":   "#1E5BB0",
        "N2":   "#006D75",
        "N3":   "#2D7A3A",
        "N4":   "#F26522",
    }

    # root
    ids_.append("root"); labels_.append("Processos AS-IS"); parents_.append("")
    values_.append(1); colors_.append(level_colors["root"])

    for r in data["N0"]:
        if r["id"] not in allowed_n0:
            continue
        ids_.append(r["id"]); labels_.append(r["nome"][:40])
        parents_.append("root"); values_.append(n1_per_n0.get(r["id"], 1))
        colors_.append(level_colors["N0"])

    for e in data["E01"]:
        if e["dst"] not in allowed_n1 or e["src"] not in allowed_n0:
            continue
        r_id = e["dst"]; nome = n1_names.get(r_id, r_id[:8])
        ids_.append(r_id); labels_.append(nome[:50])
        parents_.append(e["src"]); values_.append(n2_per_n1.get(r_id, 1))
        colors_.append(level_colors["N1"])

    for e in data["E12"]:
        if e["dst"] not in allowed_n2 or e["src"] not in allowed_n1:
            continue
        r_id = e["dst"]; nome = n2_names.get(r_id, r_id[:8])
        ids_.append(r_id); labels_.append(nome[:50])
        parents_.append(e["src"]); values_.append(n3_per_n2.get(r_id, 1))
        colors_.append(level_colors["N2"])

    for e in data["E23"]:
        if e["dst"] not in allowed_n3 or e["src"] not in allowed_n2:
            continue
        r_id = e["dst"]; nome = n3_names.get(r_id, r_id[:8])
        ids_.append(r_id); labels_.append(nome[:50])
        parents_.append(e["src"]); values_.append(n4_per_n3.get(r_id, 1))
        colors_.append(level_colors["N3"])

    for e in data["E34"]:
        if e["dst"] not in allowed_n4 or e["src"] not in allowed_n3:
            continue
        r_id = e["dst"]; nome = n4_names.get(r_id, r_id[:8])
        ids_.append(r_id); labels_.append(nome[:50])
        parents_.append(e["src"]); values_.append(1)
        colors_.append(level_colors["N4"])

    fig = go.Figure(
        go.Sunburst(
            ids=ids_,
            labels=labels_,
            parents=parents_,
            values=values_,
            marker=dict(colors=colors_, line=dict(width=1.5, color="#FFFFFF")),
            branchvalues="total",
            hovertemplate="<b>%{label}</b><br>Etapas: %{value}<extra></extra>",
            textfont=dict(family="Inter, sans-serif", size=11, color="#FFFFFF"),
            insidetextorientation="radial",
            maxdepth=5,
            leaf=dict(opacity=0.92),
            rotation=90,
        )
    )
    fig.update_layout(
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Inter, sans-serif", color="#1F2937"),
        margin=dict(t=20, l=0, r=0, b=0),
        height=640,
        showlegend=False,
    )
    return fig


# ─── Treemap Plotly ───────────────────────────────────────────────────────────
def render_treemap(data: dict, filter_n0: list[str], filter_n1: list[str]) -> go.Figure:
    """Gera treemap mostrando N0→N1→N2→N3→N4."""
    n0_names = {r["id"]: r["nome"] for r in data["N0"]}
    n1_names = {r["id"]: r["nome"] for r in data["N1"]}
    n2_names = {r["id"]: r["nome"] for r in data["N2"]}
    n3_names = {r["id"]: r["nome"] for r in data["N3"]}
    n4_names = {r["id"]: r["nome"] for r in data["N4"]}

    ids_, labels_, parents_, values_, colors_ = [], [], [], [], []

    allowed_n0 = set(
        r["id"] for r in data["N0"] if not filter_n0 or r["nome"] in filter_n0
    )
    n1_from_n0 = {e["dst"] for e in data["E01"] if e["src"] in allowed_n0}
    allowed_n1 = n1_from_n0.intersection(
        {r["id"] for r in data["N1"] if not filter_n1 or r["nome"] in filter_n1}
    )
    allowed_n2 = {e["dst"] for e in data["E12"] if e["src"] in allowed_n1}
    allowed_n3 = {e["dst"] for e in data["E23"] if e["src"] in allowed_n2}
    allowed_n4 = {e["dst"] for e in data["E34"] if e["src"] in allowed_n3}

    level_colors_tm = {
        "N0": "#0C2D6B",
        "N1": "#1E5BB0",
        "N2": "#006D75",
        "N3": "#2D7A3A",
        "N4": "#F26522",
    }

    ids_.append("root"); labels_.append("AS-IS"); parents_.append(""); values_.append(0)
    colors_.append("#0f0c29")

    for r in data["N0"]:
        if r["id"] not in allowed_n0:
            continue
        ids_.append(r["id"]); labels_.append(r["nome"])
        parents_.append("root"); values_.append(0)
        colors_.append(level_colors_tm["N0"])

    for e in data["E01"]:
        if e["dst"] not in allowed_n1 or e["src"] not in allowed_n0:
            continue
        ids_.append(e["dst"]); labels_.append(n1_names.get(e["dst"], "?"))
        parents_.append(e["src"]); values_.append(0)
        colors_.append(level_colors_tm["N1"])

    for e in data["E12"]:
        if e["dst"] not in allowed_n2 or e["src"] not in allowed_n1:
            continue
        ids_.append(e["dst"]); labels_.append(n2_names.get(e["dst"], "?"))
        parents_.append(e["src"]); values_.append(0)
        colors_.append(level_colors_tm["N2"])

    for e in data["E23"]:
        if e["dst"] not in allowed_n3 or e["src"] not in allowed_n2:
            continue
        ids_.append(e["dst"]); labels_.append(n3_names.get(e["dst"], "?"))
        parents_.append(e["src"]); values_.append(0)
        colors_.append(level_colors_tm["N3"])

    for e in data["E34"]:
        if e["dst"] not in allowed_n4 or e["src"] not in allowed_n3:
            continue
        ids_.append(e["dst"]); labels_.append(n4_names.get(e["dst"], "?"))
        parents_.append(e["src"]); values_.append(1)
        colors_.append(level_colors_tm["N4"])

    fig = go.Figure(
        go.Treemap(
            ids=ids_,
            labels=labels_,
            parents=parents_,
            values=values_,
            branchvalues="remainder",
            marker=dict(
                colors=colors_,
                line=dict(width=2, color="#FFFFFF"),
                pad=dict(t=6, l=6, r=6, b=6),
            ),
            hovertemplate="<b>%{label}</b><br>%{id}<extra></extra>",
            textfont=dict(family="Inter, sans-serif", size=12, color="#FFFFFF"),
            tiling=dict(packing="squarify", pad=4),
            maxdepth=5,
        )
    )
    fig.update_layout(
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font=dict(family="Inter, sans-serif", color="#1F2937"),
        margin=dict(t=10, l=0, r=0, b=0),
        height=620,
    )
    return fig


# ─── Sankey Plotly ────────────────────────────────────────────────────────────
def render_sankey(
    data: dict, filter_n0: list[str], filter_n1: list[str], max_n3: int = 30
) -> go.Figure:
    """Gera diagrama de Sankey N0→N1→N2→N3 (limitado para legibilidade)."""
    n0_names = {r["id"]: r["nome"] for r in data["N0"]}
    n1_names = {r["id"]: r["nome"] for r in data["N1"]}
    n2_names = {r["id"]: r["nome"] for r in data["N2"]}
    n3_names = {r["id"]: r["nome"] for r in data["N3"]}

    allowed_n0 = set(r["id"] for r in data["N0"] if not filter_n0 or r["nome"] in filter_n0)
    n1_from_n0 = {e["dst"] for e in data["E01"] if e["src"] in allowed_n0}
    allowed_n1 = n1_from_n0.intersection(
        {r["id"] for r in data["N1"] if not filter_n1 or r["nome"] in filter_n1}
    )
    allowed_n2 = {e["dst"] for e in data["E12"] if e["src"] in allowed_n1}
    allowed_n3_all = {e["dst"] for e in data["E23"] if e["src"] in allowed_n2}
    allowed_n3 = set(list(allowed_n3_all)[:max_n3])

    node_list: list[str] = []
    node_label: list[str] = []
    node_color: list[str] = []

    def _add(nid: str, name_map: dict, color: str) -> int:
        if nid not in node_list:
            node_list.append(nid)
            node_label.append(name_map.get(nid, nid[:8]))
            node_color.append(color)
        return node_list.index(nid)

    sources_, targets_, values_ = [], [], []

    for e in data["E01"]:
        if e["src"] not in allowed_n0 or e["dst"] not in allowed_n1:
            continue
        s = _add(e["src"], n0_names, "#7C3AED")
        t = _add(e["dst"], n1_names, "#2563EB")
        sources_.append(s); targets_.append(t); values_.append(2)

    for e in data["E12"]:
        if e["src"] not in allowed_n1 or e["dst"] not in allowed_n2:
            continue
        s = _add(e["src"], n1_names, "#2563EB")
        t = _add(e["dst"], n2_names, "#0891B2")
        sources_.append(s); targets_.append(t); values_.append(1.5)

    for e in data["E23"]:
        if e["src"] not in allowed_n2 or e["dst"] not in allowed_n3:
            continue
        s = _add(e["src"], n2_names, "#0891B2")
        t = _add(e["dst"], n3_names, "#059669")
        sources_.append(s); targets_.append(t); values_.append(1)

    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            node=dict(
                pad=20,
                thickness=16,
                line=dict(color="#FFFFFF", width=1),
                label=node_label,
                color=node_color,
                hoverlabel=dict(
                    bgcolor="#FFFFFF",
                    bordercolor="#0C2D6B",
                    font=dict(family="Inter", color="#1F2937", size=12),
                ),
            ),
            link=dict(
                source=sources_,
                target=targets_,
                value=values_,
                color="rgba(12,45,107,0.12)",
                hovertemplate="%{source.label} → %{target.label}<extra></extra>",
            ),
            textfont=dict(family="Inter, sans-serif", size=11, color="#1F2937"),
        )
    )
    fig.update_layout(
        paper_bgcolor="#FFFFFF",
        font=dict(family="Inter, sans-serif", color="#1F2937"),
        margin=dict(t=10, l=10, r=10, b=10),
        height=560,
    )
    return fig


# ─── Painel de Detalhes N5 ─────────────────────────────────────────────────────
def render_detail_panel(data: dict, selected_n4_name: str) -> None:
    """Mostra os atributos da etapa selecionada (N5)."""
    # Encontra o N4 pelo nome
    n4_match = next(
        (r for r in data["N4"] if r["nome"] == selected_n4_name), None
    )
    if not n4_match:
        st.info("Selecione uma Etapa (N4) na lista para ver os detalhes.")
        return

    n4_id = n4_match["id"]
    n5_link = next((e for e in data.get("E45", []) if e["src"] == n4_id), None)
    if not n5_link:
        st.info("Nenhum atributo registrado para esta etapa.")
        return

    n5 = next((r for r in data.get("N5", []) if r["id"] == n5_link["dst"]), None)
    if not n5:
        st.info("Atributos não encontrados.")
        return

    def _tags(items: list, css_class: str = "detail-tag") -> str:
        if not items:
            return '<span style="color:#6B7280;font-size:0.8rem;font-style:italic;">Não informado</span>'
        return "".join(f'<span class="{css_class}">{item}</span>' for item in items)

    desc = n5.get("descricao") or "Não informado"
    entradas_html  = _tags(n5.get("entradas") or [])
    saidas_html    = _tags(n5.get("saidas") or [])
    sistemas_html  = _tags(n5.get("sistemas_envolvidos") or [], "detail-tag detail-tag-systems")
    kpis_html      = _tags(n5.get("kpis") or [], "detail-tag detail-tag-kpi")
    oport_html     = _tags(n5.get("oportunidades_melhoria") or [], "detail-tag detail-tag-oport")

    st.markdown(
        f"""
        <div class="detail-card">
            <div style="font-size:0.65rem;font-weight:700;letter-spacing:1px;
                        text-transform:uppercase;color:#F26522;margin-bottom:8px;">
                ETAPA &mdash; Atributos Detalhados
            </div>
            <div style="font-size:1.05rem;font-weight:700;color:#0C2D6B;margin-bottom:14px;
                        border-bottom:1px solid #E2E5EA;padding-bottom:10px;">
                {selected_n4_name}
            </div>

            <div style="margin-bottom:14px;">
                <div style="font-size:0.65rem;color:#6B7280;font-weight:600;
                            letter-spacing:0.8px;text-transform:uppercase;margin-bottom:4px;">
                    Descrição
                </div>
                <div style="font-size:0.88rem;color:#374151;line-height:1.55;">{desc}</div>
            </div>

            <div style="margin-bottom:12px;">
                <div style="font-size:0.65rem;color:#6B7280;font-weight:600;
                            letter-spacing:0.8px;text-transform:uppercase;margin-bottom:6px;">
                    Entradas
                </div>
                <div>{entradas_html}</div>
            </div>

            <div style="margin-bottom:12px;">
                <div style="font-size:0.65rem;color:#6B7280;font-weight:600;
                            letter-spacing:0.8px;text-transform:uppercase;margin-bottom:6px;">
                    Saídas
                </div>
                <div>{saidas_html}</div>
            </div>

            <div style="margin-bottom:12px;">
                <div style="font-size:0.65rem;color:#6B7280;font-weight:600;
                            letter-spacing:0.8px;text-transform:uppercase;margin-bottom:6px;">
                    Sistemas Envolvidos
                </div>
                <div>{sistemas_html}</div>
            </div>

            <div style="margin-bottom:12px;">
                <div style="font-size:0.65rem;color:#6B7280;font-weight:600;
                            letter-spacing:0.8px;text-transform:uppercase;margin-bottom:6px;">
                    KPIs
                </div>
                <div>{kpis_html}</div>
            </div>

            <div>
                <div style="font-size:0.65rem;color:#6B7280;font-weight:600;
                            letter-spacing:0.8px;text-transform:uppercase;margin-bottom:6px;">
                    Oportunidades de Melhoria
                </div>
                <div>{oport_html}</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ─── Tabela Explorer ──────────────────────────────────────────────────────────
def render_table(data: dict, filter_n0: list[str], filter_n1: list[str]) -> None:
    """Renderiza tabela flat navegável com todos os nós N0–N4 + atributos N5."""
    n0_idx = {r["id"]: r["nome"] for r in data["N0"]}
    n1_idx = {r["id"]: r["nome"] for r in data["N1"]}
    n2_idx = {r["id"]: r["nome"] for r in data["N2"]}
    n3_idx = {r["id"]: r["nome"] for r in data["N3"]}
    n4_idx = {r["id"]: r["nome"] for r in data["N4"]}
    n5_idx = {r["id"]: r for r in data.get("N5", [])}

    # Reconstrói relações
    e34_map = {e["dst"]: e["src"] for e in data["E34"]}  # n4_id → n3_id
    e23_map = {e["dst"]: e["src"] for e in data["E23"]}
    e12_map = {e["dst"]: e["src"] for e in data["E12"]}
    e01_map = {e["dst"]: e["src"] for e in data["E01"]}
    e45_map = {e["src"]: e["dst"] for e in data.get("E45", [])}

    allowed_n0 = set(r["id"] for r in data["N0"] if not filter_n0 or r["nome"] in filter_n0)
    n1_from_n0 = {e["dst"] for e in data["E01"] if e["src"] in allowed_n0}
    allowed_n1 = n1_from_n0.intersection(
        {r["id"] for r in data["N1"] if not filter_n1 or r["nome"] in filter_n1}
    )
    allowed_n2 = {e["dst"] for e in data["E12"] if e["src"] in allowed_n1}
    allowed_n3 = {e["dst"] for e in data["E23"] if e["src"] in allowed_n2}
    allowed_n4 = {e["dst"] for e in data["E34"] if e["src"] in allowed_n3}

    rows_flat = []
    for n4_id in allowed_n4:
        n3_id = e34_map.get(n4_id, "")
        n2_id = e23_map.get(n3_id, "")
        n1_id = e12_map.get(n2_id, "")
        n0_id = e01_map.get(n1_id, "")
        n5_id = e45_map.get(n4_id)
        n5 = n5_idx.get(n5_id, {}) if n5_id else {}

        rows_flat.append({
            "Frente (N0)":          n0_idx.get(n0_id, ""),
            "Macro Processo (N1)":  n1_idx.get(n1_id, ""),
            "Processo (N2)":        n2_idx.get(n2_id, ""),
            "Tarefa (N3)":          n3_idx.get(n3_id, ""),
            "Etapa (N4)":           n4_idx.get(n4_id, ""),
            "Descrição":            n5.get("descricao", ""),
            "Entradas":             " | ".join(n5.get("entradas", []) or []),
            "Saídas":               " | ".join(n5.get("saidas", []) or []),
            "Sistemas":             " | ".join(n5.get("sistemas_envolvidos", []) or []),
            "KPIs":                 " | ".join(n5.get("kpis", []) or []),
            "Melhorias":            " | ".join(n5.get("oportunidades_melhoria", []) or []),
        })

    if not rows_flat:
        st.info("Nenhuma linha encontrada com os filtros aplicados.")
        return

    df = pd.DataFrame(rows_flat).sort_values(
        ["Frente (N0)", "Macro Processo (N1)", "Processo (N2)", "Tarefa (N3)", "Etapa (N4)"]
    )

    search = st.text_input(
        "🔍 Busca livre na tabela",
        placeholder="Digite para filtrar qualquer coluna…",
        key="tbl_search",
    )
    if search:
        mask = df.apply(
            lambda col: col.astype(str).str.lower().str.contains(search.lower(), na=False)
        ).any(axis=1)
        df = df[mask]

    st.caption(f"{len(df)} linhas exibidas")
    st.dataframe(
        df,
        use_container_width=True,
        height=min(520, 40 + len(df) * 36),
        column_config={
            "Descrição": st.column_config.TextColumn(width="large"),
            "Etapa (N4)": st.column_config.TextColumn(width="medium"),
        },
    )

    csv = df.to_csv(index=False, encoding="utf-8-sig")
    st.download_button(
        "⬇️  Exportar CSV",
        data=csv.encode("utf-8-sig"),
        file_name="processos_as_is.csv",
        mime="text/csv",
        use_container_width=False,
    )


# ─── Métricas HTML ────────────────────────────────────────────────────────────
def _metric_html(emoji: str, value: int, label: str, color: str) -> str:
    accent = 'accent' if color == '#F26522' else ''
    return (
        f'<div class="metric-card {accent}">'
        f'<div class="metric-emoji">{emoji}</div>'
        f'<div class="metric-value" style="color:{color}">{value}</div>'
        f'<div class="metric-label">{label}</div>'
        f'</div>'
    )


# ─── Legendinha na sidebar ────────────────────────────────────────────────────
def _sidebar_legend() -> None:
    st.sidebar.markdown('<div class="section-title">Legenda de Níveis</div>', unsafe_allow_html=True)
    for level, cfg in LEVEL_CONFIG.items():
        if level == "N5":
            continue
        st.sidebar.markdown(
            f'<div class="legend-item">'
            f'<div class="legend-dot" style="background:{cfg["color"]};"></div>'
            f'<b>{level}</b> — {cfg["label"]}'
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

    # ── Carrega dados ─────────────────────────────────────────────────────────
    with st.spinner("Carregando grafo de processos…"):
        data, source = load_data()

    if not data or not data.get("N0"):
        st.error(
            "⚠️ Não foi possível carregar dados do Spanner nem do arquivo de debug. "
            "Verifique as credenciais e a conectividade."
        )
        if st.session_state.get("_spanner_error"):
            with st.expander("Detalhes do erro"):
                st.code(st.session_state["_spanner_error"])
        return

    # ── Hero Header ───────────────────────────────────────────────────────────
    source_badge = (
        '<span class="status-ok">● Spanner (ao vivo)</span>'
        if source == "spanner"
        else '<span class="status-warn">● Arquivo local (debug_last_json.json)</span>'
    )
    st.markdown(
        f"""
        <div class="hero">
            <div class="hero-title">Mapa de Processos AS-IS</div>
            <div class="hero-subtitle">
                Visualização de Grafo Hierárquico &nbsp;·&nbsp; Google Cloud Spanner ProcessosGraph
            </div>
            <span class="hero-badge">N0 &rarr; N1 &rarr; N2 &rarr; N3 &rarr; N4 &rarr; N5</span>
            &nbsp;&nbsp;{source_badge}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Sidebar — Filtros e Configurações ─────────────────────────────────────
    st.sidebar.markdown(
        '<div class="sidebar-header">'
        '<div style="font-size:0.65rem;font-weight:700;letter-spacing:1px;'
        'text-transform:uppercase;color:#93B8E8;margin-bottom:4px;">Cliente</div>'
        '<div style="font-size:1rem;font-weight:800;color:#FFFFFF;">Processos AS-IS</div>'
        '<div style="font-size:0.75rem;color:#93B8E8;margin-top:2px;">Análise de Processos</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    st.sidebar.markdown('<div class="section-title">Filtros</div>', unsafe_allow_html=True)

    all_n0 = sorted({r["nome"] for r in data["N0"]})
    all_n1 = sorted({r["nome"] for r in data["N1"]})

    filter_n0 = st.sidebar.multiselect(
        "🏛️ Frente (N0)",
        options=all_n0,
        default=[],
        placeholder="Todas as frentes",
    )
    filter_n1 = st.sidebar.multiselect(
        "⚙️ Macro Processo (N1)",
        options=all_n1,
        default=[],
        placeholder="Todos os macro-processos",
    )

    st.sidebar.divider()
    st.sidebar.markdown('<div class="section-title">Grafo Interativo</div>', unsafe_allow_html=True)

    layout_opt = st.sidebar.selectbox(
        "Layout",
        ["Hierárquico (Top-Down)", "Hierárquico (Left-Right)", "Force-Directed (Físico)"],
        index=0,
    )
    show_n5 = st.sidebar.toggle("Mostrar Atributos (N5)", value=False)
    physics  = st.sidebar.toggle("Ativar Física (Force)", value=False, disabled=layout_opt != "Force-Directed (Físico)")
    node_labels = st.sidebar.toggle("Exibir rótulos nos nós", value=True)
    graph_height = st.sidebar.slider("Altura do Grafo (px)", 400, 900, 650, 50)

    st.sidebar.divider()
    _sidebar_legend()

    st.sidebar.divider()
    if st.sidebar.button("🔄 Atualizar dados (limpar cache)"):
        st.cache_data.clear()
        st.rerun()

    # ── Métricas ──────────────────────────────────────────────────────────────
    n_n0 = len(data["N0"]); n_n1 = len(data["N1"]); n_n2 = len(data["N2"])
    n_n3 = len(data["N3"]); n_n4 = len(data["N4"])
    n_edges = (
        len(data["E01"]) + len(data["E12"]) + len(data["E23"])
        + len(data["E34"]) + len(data.get("E45", []))
    )
    n_n5 = len(data.get("N5", []))

    metrics_html = (
        '<div class="metric-row">'
        + _metric_html("🏛️", n_n0,    "Frentes",      "#0C2D6B")
        + _metric_html("⚙️", n_n1,    "Macro Proc.",  "#0C2D6B")
        + _metric_html("🔄", n_n2,    "Processos",    "#0C2D6B")
        + _metric_html("📋", n_n3,    "Tarefas",      "#0C2D6B")
        + _metric_html("▶️", n_n4,    "Etapas",       "#F26522")
        + _metric_html("📝", n_n5,    "Atributos",    "#6B7280")
        + _metric_html("🔗", n_edges, "Arestas",      "#6B7280")
        + "</div>"
    )
    st.markdown(metrics_html, unsafe_allow_html=True)

    # ── Monta o grafo NetworkX com filtros ────────────────────────────────────
    G = build_nx_graph(data, show_n5, filter_n0, filter_n1)

    n_nodes_filtered = G.number_of_nodes()
    n_edges_filtered = G.number_of_edges()
    st.caption(
        f"Grafo filtrado: **{n_nodes_filtered} nós** · **{n_edges_filtered} arestas**"
        + (f"  ·  _(filtro ativo: {', '.join(filter_n0 + filter_n1)})_" if filter_n0 or filter_n1 else "")
    )

    if n_nodes_filtered == 0:
        st.warning("Nenhum nó encontrado com os filtros atuais. Remova ou altere os filtros.")
        return

    # ── Tabs ──────────────────────────────────────────────────────────────────
    tab_graph, tab_sunburst, tab_treemap, tab_sankey, tab_table, tab_detail = st.tabs([
        "🌐  Grafo Interativo",
        "🌈  Sunburst",
        "📊  Treemap",
        "🌊  Sankey",
        "📋  Tabela",
        "🔍  Detalhe da Etapa",
    ])

    # ── TAB 1: Grafo pyvis ────────────────────────────────────────────────────
    with tab_graph:
        st.markdown(
            '<div class="section-title">Grafo de Processos AS-IS — Hierarquia N0 → N1 → N2 → N3 → N4</div>',
            unsafe_allow_html=True,
        )
        if n_nodes_filtered > 500:
            st.warning(
                f"⚠️ O grafo possui {n_nodes_filtered} nós — a renderização pode ser lenta. "
                "Use os filtros para reduzir o escopo ou desative os níveis N4/N5."
            )

        html_str = render_pyvis(G, graph_height, physics, layout_opt, node_labels)
        components.html(html_str, height=graph_height + 10, scrolling=False)

        st.markdown(
            """
            <div style="font-size:0.72rem;color:#9CA3AF;margin-top:6px;
                        padding: 6px 10px; background:#FFFFFF; border-radius:4px;
                        border:1px solid #E2E5EA; display:inline-block;">
            Arraste para mover &nbsp;·&nbsp; Scroll para zoom &nbsp;·&nbsp; Hover para detalhes
            &emsp;
            <b style="color:#0C2D6B">★ N0-Frente</b>&nbsp;
            <b style="color:#1E5BB0">⬡ N1-Macro</b>&nbsp;
            <b style="color:#006D75">◯ N2-Proc</b>&nbsp;
            <b style="color:#2D7A3A">▪ N3-Tarefa</b>&nbsp;
            <b style="color:#F26522">● N4-Etapa</b>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── TAB 2: Sunburst ───────────────────────────────────────────────────────
    with tab_sunburst:
        st.markdown('<div class="section-title">Hierarquia em Sunburst — clique para explorar</div>', unsafe_allow_html=True)
        fig_sb = render_sunburst(data, filter_n0, filter_n1)
        st.plotly_chart(fig_sb, use_container_width=True)

    # ── TAB 3: Treemap ────────────────────────────────────────────────────────
    with tab_treemap:
        st.markdown('<div class="section-title">Treemap — área proporcional ao número de Etapas</div>', unsafe_allow_html=True)
        fig_tm = render_treemap(data, filter_n0, filter_n1)
        st.plotly_chart(fig_tm, use_container_width=True)

    # ── TAB 4: Sankey ─────────────────────────────────────────────────────────
    with tab_sankey:
        st.markdown('<div class="section-title">Fluxo Sankey — N0 → N1 → N2 → N3</div>', unsafe_allow_html=True)
        max_n3_ski = st.slider("Máx. de Tarefas (N3) no Sankey", 10, 80, 30, 5, key="sk_max")
        fig_sk = render_sankey(data, filter_n0, filter_n1, max_n3=max_n3_ski)
        st.plotly_chart(fig_sk, use_container_width=True)
        st.caption("Cada banda representa um fluxo hierárquico — a espessura indica o peso relativo.")

    # ── TAB 5: Tabela ─────────────────────────────────────────────────────────
    with tab_table:
        st.markdown('<div class="section-title">Explorador de Dados — todas as etapas + atributos</div>', unsafe_allow_html=True)
        render_table(data, filter_n0, filter_n1)

    # ── TAB 6: Detalhe ────────────────────────────────────────────────────────
    with tab_detail:
        st.markdown('<div class="section-title">Detalhes da Etapa — atributos N5</div>', unsafe_allow_html=True)

        # Filtrar N4 visíveis
        allowed_n0s = set(r["id"] for r in data["N0"] if not filter_n0 or r["nome"] in filter_n0)
        n1_ids = {e["dst"] for e in data["E01"] if e["src"] in allowed_n0s}
        n1_ids_f = n1_ids.intersection(
            {r["id"] for r in data["N1"] if not filter_n1 or r["nome"] in filter_n1}
        )
        n2_ids = {e["dst"] for e in data["E12"] if e["src"] in n1_ids_f}
        n3_ids = {e["dst"] for e in data["E23"] if e["src"] in n2_ids}
        n4_ids = {e["dst"] for e in data["E34"] if e["src"] in n3_ids}
        n4_names_list = sorted(
            r["nome"] for r in data["N4"] if r["id"] in n4_ids
        )

        if not n4_names_list:
            st.info("Nenhuma etapa encontrada com os filtros atuais.")
        else:
            selected_n4 = st.selectbox(
                "Selecione uma Etapa (N4)",
                options=n4_names_list,
                index=0,
                key="detail_select",
            )
            render_detail_panel(data, selected_n4)


if __name__ == "__main__":
    main()
