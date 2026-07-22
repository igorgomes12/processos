"""
Testes unitários para document_processor/tools/postgres_tool.py.
Cobre: _extract_json_from_text, _as_list, _make_id, _persistir_no_postgres,
       save_to_postgres_from_state, _upsert_mermaid_sync.
Todo acesso real ao Cloud SQL / pg8000 é mockado.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest

import document_processor.tools.postgres_tool as pg_module
from document_processor.tools.postgres_tool import (
    _extract_json_from_text,
    _as_list,
    _make_id,
    _persistir_no_postgres,
    save_to_postgres_from_state,
    _upsert_mermaid_sync,
)


VALID_JSON_STR = json.dumps({
    "schemaVersion": "1.0.0",
    "rows": [
        {
            "N0": "Frente A", "N1": "Macro 1", "N2": "Processo X",
            "N3": "Tarefa 1", "N4": "Etapa 1",
            "descricao": "desc", "entradas": ["e1"], "saidas": ["s1"],
            "sistemasEnvolvidos": ["sis1"], "kpis": ["k1"],
            "oportunidadesMelhoria": ["o1"],
        }
    ],
})


def _mock_tool_context(state: dict) -> MagicMock:
    tc = MagicMock()
    tc.state = state
    return tc


def _make_conn_mock():
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value = cur
    return conn, cur


# ─── _extract_json_from_text ─────────────────────────────────────────────────

class TestExtractJsonFromText:
    def test_pure_json_extracted(self):
        result = _extract_json_from_text(VALID_JSON_STR)
        assert result is not None
        assert "rows" in json.loads(result)

    def test_json_with_prefix_and_suffix(self):
        text = "Aqui está:\n" + VALID_JSON_STR + "\nFim."
        result = _extract_json_from_text(text)
        assert result is not None

    def test_empty_string_returns_none(self):
        assert _extract_json_from_text("") is None

    def test_no_rows_key_returns_none(self):
        j = json.dumps({"schemaVersion": "1.0.0"})
        assert _extract_json_from_text(j) is None

    def test_invalid_json_returns_none(self):
        assert _extract_json_from_text("{invalid}") is None

    def test_none_input_returns_none(self):
        assert _extract_json_from_text(None) is None


# ─── _as_list ─────────────────────────────────────────────────────────────────

class TestAsList:
    def test_list_returns_list_of_strings(self):
        assert _as_list(["a", "b"]) == ["a", "b"]

    def test_none_returns_empty_list(self):
        assert _as_list(None) == []

    def test_empty_string_returns_empty_list(self):
        assert _as_list("") == []

    def test_scalar_returns_single_item_list(self):
        assert _as_list("x") == ["x"]

    def test_filters_none_items_from_list(self):
        assert _as_list(["a", None, "b"]) == ["a", "b"]


# ─── _make_id ────────────────────────────────────────────────────────────────

class TestMakeId:
    def test_deterministic_same_input_same_id(self):
        assert _make_id("Frente A", "Macro 1") == _make_id("Frente A", "Macro 1")

    def test_different_input_different_id(self):
        assert _make_id("Frente A") != _make_id("Frente B")

    def test_returns_valid_uuid_string(self):
        result = _make_id("X", "Y")
        uuid.UUID(result)  # não deve levantar exceção


# ─── _persistir_no_postgres ───────────────────────────────────────────────────

class TestPersistirNoPostgres:
    def test_no_rows_returns_message(self):
        result = _persistir_no_postgres({"rows": []})
        assert "não contém linhas" in result

    def test_success_commits_transaction(self):
        conn, cur = _make_conn_mock()
        with patch.object(pg_module, "_get_connection", return_value=conn):
            result = _persistir_no_postgres(json.loads(VALID_JSON_STR))
        conn.commit.assert_called_once()
        conn.close.assert_called_once()
        assert "1 etapa(s) persistida(s)" in result

    def test_exception_rolls_back_transaction(self):
        conn, cur = _make_conn_mock()
        cur.executemany.side_effect = Exception("db error")
        with patch.object(pg_module, "_get_connection", return_value=conn):
            with pytest.raises(Exception):
                _persistir_no_postgres(json.loads(VALID_JSON_STR))
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()


# ─── save_to_postgres_from_state ──────────────────────────────────────────────

class TestSaveToPostgresFromState:
    def test_success_returns_summary_message(self):
        tc = _mock_tool_context({"pdf_input_json": VALID_JSON_STR})
        with patch.object(pg_module, "_persistir_no_postgres", return_value="Postgres: ok"):
            result = asyncio.run(save_to_postgres_from_state(tc))
        assert result == "Postgres: ok"

    def test_empty_state_returns_warning(self):
        tc = _mock_tool_context({"pdf_input_json": ""})
        result = asyncio.run(save_to_postgres_from_state(tc))
        assert "AVISO" in result

    def test_missing_key_returns_warning(self):
        tc = _mock_tool_context({})
        result = asyncio.run(save_to_postgres_from_state(tc))
        assert "AVISO" in result

    def test_invalid_json_returns_error(self):
        tc = _mock_tool_context({"pdf_input_json": "not json at all"})
        result = asyncio.run(save_to_postgres_from_state(tc))
        assert "ERRO" in result

    def test_timeout_returns_error(self):
        tc = _mock_tool_context({"pdf_input_json": VALID_JSON_STR})
        with patch(
            "document_processor.tools.postgres_tool.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = asyncio.run(save_to_postgres_from_state(tc))
        assert "timeout" in result.lower()

    def test_persistence_exception_returns_error(self):
        tc = _mock_tool_context({"pdf_input_json": VALID_JSON_STR})
        with patch.object(
            pg_module, "_persistir_no_postgres", side_effect=Exception("conn refused")
        ):
            result = asyncio.run(save_to_postgres_from_state(tc))
        assert "ERRO" in result


# ─── _upsert_mermaid_sync ──────────────────────────────────────────────────────

class TestUpsertMermaidSync:
    def test_empty_paths_returns_message(self):
        result = _upsert_mermaid_sync([], "graph TD; A-->B;")
        assert "Nenhum N2" in result

    def test_empty_script_returns_message(self):
        result = _upsert_mermaid_sync([("F", "M", "P")], "")
        assert "vazio" in result

    def test_success_commits_transaction(self):
        conn, cur = _make_conn_mock()
        with patch.object(pg_module, "_get_connection", return_value=conn):
            result = _upsert_mermaid_sync(
                [("Frente A", "Macro 1", "Processo X")], "graph TD; A-->B;"
            )
        conn.commit.assert_called_once()
        conn.close.assert_called_once()
        assert "Processo X" in result

    def test_exception_rolls_back_transaction(self):
        conn, cur = _make_conn_mock()
        cur.executemany.side_effect = Exception("db error")
        with patch.object(pg_module, "_get_connection", return_value=conn):
            with pytest.raises(Exception):
                _upsert_mermaid_sync(
                    [("Frente A", "Macro 1", "Processo X")], "graph TD; A-->B;"
                )
        conn.rollback.assert_called_once()
