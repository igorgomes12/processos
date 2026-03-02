"""
Testes unitários para as_is/tools/firestore_tool.py.
Cobre: save_to_firestore_tool, _write_to_firestore, _get_firestore_client.
Todo acesso real ao Firestore é mockado.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

import as_is.tools.firestore_tool as ft_module
from as_is.tools.firestore_tool import save_to_firestore_tool


# ─── Fixtures ─────────────────────────────────────────────────────────────────

VALID_JSON_STR = json.dumps({
    "schemaVersion": "1.0.0",
    "meta": {},
    "columns": [],
    "rows": [],
})


def _mock_tool_context():
    return MagicMock()


def _make_doc_ref(doc_id: str = "doc-123"):
    doc_ref = MagicMock()
    doc_ref.id = doc_id
    return doc_ref


def _make_firestore_client(doc_id: str = "doc-abc"):
    client = MagicMock()
    doc_ref = _make_doc_ref(doc_id)
    client.collection.return_value.add.return_value = (None, doc_ref)
    return client


# ─── save_to_firestore_tool ──────────────────────────────────────────────────

class TestSaveToFirestoreTool:
    def test_success_returns_status_success(self):
        client = _make_firestore_client("new-id")
        tc = _mock_tool_context()
        with patch.object(ft_module, "_get_firestore_client", return_value=client):
            result = asyncio.run(save_to_firestore_tool(VALID_JSON_STR, tc))
        assert result["status"] == "success"

    def test_success_message_contains_doc_id(self):
        client = _make_firestore_client("doc-xyz")
        tc = _mock_tool_context()
        with patch.object(ft_module, "_get_firestore_client", return_value=client):
            result = asyncio.run(save_to_firestore_tool(VALID_JSON_STR, tc))
        assert "doc-xyz" in result["message"]

    def test_success_message_contains_collection_name(self):
        client = _make_firestore_client()
        tc = _mock_tool_context()
        with patch.object(ft_module, "_get_firestore_client", return_value=client):
            result = asyncio.run(save_to_firestore_tool(VALID_JSON_STR, tc))
        assert "processos" in result["message"]

    def test_invalid_json_returns_error(self):
        tc = _mock_tool_context()
        result = asyncio.run(save_to_firestore_tool("{invalid json}", tc))
        assert result["status"] == "error"
        assert "JSON" in result["message"]

    def test_empty_string_returns_error(self):
        tc = _mock_tool_context()
        result = asyncio.run(save_to_firestore_tool("", tc))
        assert result["status"] == "error"

    def test_firestore_exception_returns_error(self):
        client = MagicMock()
        client.collection.return_value.add.side_effect = Exception("Connection failed")
        tc = _mock_tool_context()
        with patch.object(ft_module, "_get_firestore_client", return_value=client):
            result = asyncio.run(save_to_firestore_tool(VALID_JSON_STR, tc))
        assert result["status"] == "error"
        assert "Firestore" in result["message"]

    def test_timeout_returns_error_with_timeout_message(self):
        tc = _mock_tool_context()
        with patch(
            "as_is.tools.firestore_tool.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = asyncio.run(save_to_firestore_tool(VALID_JSON_STR, tc))
        assert result["status"] == "error"
        assert "Timeout" in result["message"]

    def test_firestore_collection_called_with_correct_name(self):
        client = _make_firestore_client()
        tc = _mock_tool_context()
        with patch.object(ft_module, "_get_firestore_client", return_value=client):
            asyncio.run(save_to_firestore_tool(VALID_JSON_STR, tc))
        client.collection.assert_called_with("processos")

    def test_data_passed_to_firestore_is_parsed_dict(self):
        client = _make_firestore_client()
        tc = _mock_tool_context()
        data = {"schemaVersion": "1.0.0", "rows": [{"N0": "X"}]}
        json_str = json.dumps(data)
        with patch.object(ft_module, "_get_firestore_client", return_value=client):
            asyncio.run(save_to_firestore_tool(json_str, tc))
        actual_data = client.collection.return_value.add.call_args[0][0]
        assert actual_data == data

    def test_json_with_unicode_is_accepted(self):
        client = _make_firestore_client()
        tc = _mock_tool_context()
        data = {"nome": "Processo de Açúcar", "rows": []}
        json_str = json.dumps(data, ensure_ascii=False)
        with patch.object(ft_module, "_get_firestore_client", return_value=client):
            result = asyncio.run(save_to_firestore_tool(json_str, tc))
        assert result["status"] == "success"


# ─── _get_firestore_client (singleton) ───────────────────────────────────────

class TestGetFirestoreClient:
    def test_singleton_same_instance_returned(self):
        """O cliente deve ser criado apenas uma vez (singleton)."""
        # Reset singleton
        ft_module._firestore_client = None
        mock_client = MagicMock()
        with patch("as_is.tools.firestore_tool.service_account.Credentials.from_service_account_file", return_value=MagicMock()), \
             patch("as_is.tools.firestore_tool.firestore.Client", return_value=mock_client):
            c1 = ft_module._get_firestore_client()
            c2 = ft_module._get_firestore_client()
        assert c1 is c2
        # Reset para não contaminar outros testes
        ft_module._firestore_client = None

    def test_client_created_with_correct_project(self):
        ft_module._firestore_client = None
        mock_creds = MagicMock()
        mock_client = MagicMock()
        with patch("as_is.tools.firestore_tool.service_account.Credentials.from_service_account_file", return_value=mock_creds), \
             patch("as_is.tools.firestore_tool.firestore.Client", return_value=mock_client) as mock_constructor:
            ft_module._get_firestore_client()
        call_kwargs = mock_constructor.call_args[1]
        assert call_kwargs["project"] == "steady-computer-487217-p6"
        ft_module._firestore_client = None
