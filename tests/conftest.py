"""
Fixtures compartilhadas entre todos os módulos de teste.
"""
from __future__ import annotations

import json
import os
import pytest


# ─── JSON canônico mínimo (válido para todos os testes) ──────────────────────

MINIMAL_JSON = {
    "schemaVersion": "1.0.0",
    "meta": {
        "model": "N0-N4+atributos",
        "createdAt": "2026-02-19T18:04:15-03:00",
        "locale": "pt-BR",
        "output": {"type": "spreadsheet", "recommendedSheetName": "Processos"},
    },
    "columns": [
        {"key": "N0", "label": "Frente (N0)"},
        {"key": "N1", "label": "Macro processo (N1)"},
        {"key": "N2", "label": "Processo (N2)"},
        {"key": "N3", "label": "Tarefa (N3)"},
        {"key": "N4", "label": "Etapa (N4)"},
        {"key": "descricao", "label": "Descrição"},
        {"key": "entradas", "label": "Entradas"},
        {"key": "saidas", "label": "Saídas"},
        {"key": "sistemasEnvolvidos", "label": "Sistemas Envolvidos"},
        {"key": "kpis", "label": "KPIs"},
        {"key": "oportunidadesMelhoria", "label": "Oportunidades de melhorias"},
    ],
    "rows": [
        {
            "rowId": "r1",
            "N0": "Frente A",
            "N1": "Macro 1",
            "N2": "Processo X",
            "N3": "Tarefa 1",
            "N4": "Etapa 1",
            "descricao": "Descrição da etapa",
            "entradas": ["Entrada 1", "Entrada 2"],
            "saidas": ["Saída 1"],
            "sistemasEnvolvidos": ["Sistema A"],
            "kpis": ["KPI 1"],
            "oportunidadesMelhoria": ["Oportunidade 1"],
            "notes": "nota",
            "tags": ["tag1"],
        }
    ],
    "exportHints": {
        "arrayJoiner": "\n",
        "emptyArrayAs": "",
        "keepColumnOrderAsDefined": True,
    },
}

MINIMAL_JSON_STR = json.dumps(MINIMAL_JSON, ensure_ascii=False)


@pytest.fixture
def minimal_json_str():
    return MINIMAL_JSON_STR


@pytest.fixture
def minimal_json_dict():
    return MINIMAL_JSON


@pytest.fixture
def json_file(tmp_path):
    """Cria um arquivo JSON temporário com o JSON canônico mínimo."""
    p = tmp_path / "processos.json"
    p.write_text(MINIMAL_JSON_STR, encoding="utf-8")
    return str(p)


@pytest.fixture
def xlsx_path(tmp_path):
    """Retorna um caminho temporário para o arquivo XLSX."""
    return str(tmp_path / "processos.xlsx")
