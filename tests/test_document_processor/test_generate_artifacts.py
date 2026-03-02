"""
Testes unitários para document_processor/tools/generate_artifacts.py.
Cobre: _extract_json_from_text, _build_xlsx_bytes, generate_xlsx_from_state,
       generate_pdf_from_state.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from document_processor.tools.generate_artifacts import (
    _extract_json_from_text,
    _build_xlsx_bytes,
    generate_xlsx_from_state,
    generate_pdf_from_state,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

VALID_JSON = json.dumps({
    "schemaVersion": "1.0.0",
    "meta": {"output": {"recommendedSheetName": "Processos"}},
    "columns": [{"key": "N0", "label": "N0"}],
    "rows": [{"N0": "Frente A"}],
    "exportHints": {"arrayJoiner": "\n", "emptyArrayAs": ""},
})


def _make_tool_context(state: dict) -> MagicMock:
    tc = MagicMock()
    tc.state = state
    tc.save_artifact = AsyncMock(return_value=1)
    return tc


# ─── _extract_json_from_text ─────────────────────────────────────────────────

class TestExtractJsonFromText:
    def test_pure_json_extracted(self):
        result = _extract_json_from_text(VALID_JSON)
        assert result is not None
        parsed = json.loads(result)
        assert "columns" in parsed and "rows" in parsed

    def test_json_with_prefix_text(self):
        text = "Aqui está o JSON gerado:\n" + VALID_JSON
        result = _extract_json_from_text(text)
        assert result is not None
        parsed = json.loads(result)
        assert "rows" in parsed

    def test_json_with_suffix_text(self):
        text = VALID_JSON + "\nProcessamento concluído."
        result = _extract_json_from_text(text)
        assert result is not None

    def test_json_with_both_prefix_and_suffix(self):
        text = "Prefixo\n" + VALID_JSON + "\nSufixo"
        result = _extract_json_from_text(text)
        assert result is not None

    def test_empty_string_returns_none(self):
        assert _extract_json_from_text("") is None

    def test_whitespace_only_returns_none(self):
        assert _extract_json_from_text("   ") is None

    def test_no_json_braces_returns_none(self):
        assert _extract_json_from_text("sem nenhum JSON aqui") is None

    def test_invalid_json_returns_none(self):
        assert _extract_json_from_text("{invalid json}") is None

    def test_json_without_columns_returns_none(self):
        j = json.dumps({"schemaVersion": "1.0.0", "rows": []})
        assert _extract_json_from_text(j) is None

    def test_json_without_rows_returns_none(self):
        j = json.dumps({"schemaVersion": "1.0.0", "columns": []})
        assert _extract_json_from_text(j) is None

    def test_none_input_returns_none(self):
        assert _extract_json_from_text(None) is None

    def test_json_in_markdown_fences(self):
        text = "```json\n" + VALID_JSON + "\n```"
        result = _extract_json_from_text(text)
        # Deve extrair o JSON dentro dos backticks
        assert result is not None

    def test_returns_string_type(self):
        result = _extract_json_from_text(VALID_JSON)
        assert isinstance(result, str)


# ─── _build_xlsx_bytes ───────────────────────────────────────────────────────

class TestBuildXlsxBytes:
    def test_returns_bytes(self):
        result = _build_xlsx_bytes(VALID_JSON)
        assert isinstance(result, bytes)

    def test_returns_non_empty_bytes(self):
        result = _build_xlsx_bytes(VALID_JSON)
        assert len(result) > 0

    def test_xlsx_magic_bytes(self):
        result = _build_xlsx_bytes(VALID_JSON)
        # Arquivo XLSX começa com PK (ZIP header)
        assert result[:2] == b"PK"

    def test_raises_on_invalid_json(self):
        with pytest.raises(Exception):
            _build_xlsx_bytes("{invalid}")

    def test_raises_on_missing_columns(self):
        j = json.dumps({"columns": [], "rows": []})
        with pytest.raises(ValueError):
            _build_xlsx_bytes(j)


# ─── generate_xlsx_from_state ────────────────────────────────────────────────

class TestGenerateXlsxFromState:
    def test_success_with_valid_json_in_state(self):
        tc = _make_tool_context({"pdf_input_json": VALID_JSON})
        result = asyncio.run(generate_xlsx_from_state(tc))
        assert result["status"] == "success"

    def test_success_message_contains_filename(self):
        tc = _make_tool_context({"pdf_input_json": VALID_JSON})
        result = asyncio.run(generate_xlsx_from_state(tc))
        assert "processos.xlsx" in result["message"]

    def test_success_message_contains_versao(self):
        tc = _make_tool_context({"pdf_input_json": VALID_JSON})
        result = asyncio.run(generate_xlsx_from_state(tc))
        assert "versão" in result["message"]

    def test_save_artifact_called(self):
        tc = _make_tool_context({"pdf_input_json": VALID_JSON})
        asyncio.run(generate_xlsx_from_state(tc))
        tc.save_artifact.assert_called_once()
        call_kwargs = tc.save_artifact.call_args[1]
        assert call_kwargs["filename"] == "processos.xlsx"

    def test_error_when_state_empty(self):
        tc = _make_tool_context({"pdf_input_json": ""})
        result = asyncio.run(generate_xlsx_from_state(tc))
        assert result["status"] == "error"
        assert "vazio" in result["message"]

    def test_error_when_key_missing_from_state(self):
        tc = _make_tool_context({})
        result = asyncio.run(generate_xlsx_from_state(tc))
        assert result["status"] == "error"

    def test_error_when_json_has_no_columns(self):
        j = json.dumps({"columns": [], "rows": []})
        tc = _make_tool_context({"pdf_input_json": j})
        result = asyncio.run(generate_xlsx_from_state(tc))
        assert result["status"] == "error"

    def test_json_with_prefix_text_still_works(self):
        text = "Status: OK\n" + VALID_JSON
        tc = _make_tool_context({"pdf_input_json": text})
        result = asyncio.run(generate_xlsx_from_state(tc))
        assert result["status"] == "success"

    def test_timeout_returns_error(self):
        tc = _make_tool_context({"pdf_input_json": VALID_JSON})
        with patch(
            "document_processor.tools.generate_artifacts.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = asyncio.run(generate_xlsx_from_state(tc))
        assert result["status"] == "error"
        assert "Timeout" in result["message"]


# ─── generate_pdf_from_state ─────────────────────────────────────────────────

SIMPLE_MARKDOWN = """# Processo AS-IS

## Descrição
Este é um documento de processo simples para testes.

## Fluxo
- Etapa 1
- Etapa 2
"""


class TestGeneratePdfFromState:
    def test_success_with_valid_markdown(self):
        tc = _make_tool_context({"pdf_markdown": SIMPLE_MARKDOWN})
        result = asyncio.run(generate_pdf_from_state(tc))
        assert result["status"] == "success"

    def test_success_message_contains_filename(self):
        tc = _make_tool_context({"pdf_markdown": SIMPLE_MARKDOWN})
        result = asyncio.run(generate_pdf_from_state(tc))
        assert "documento_processo.pdf" in result["message"]

    def test_save_artifact_called(self):
        tc = _make_tool_context({"pdf_markdown": SIMPLE_MARKDOWN})
        asyncio.run(generate_pdf_from_state(tc))
        tc.save_artifact.assert_called_once()
        call_kwargs = tc.save_artifact.call_args[1]
        assert call_kwargs["filename"] == "documento_processo.pdf"

    def test_error_when_state_empty(self):
        tc = _make_tool_context({"pdf_markdown": ""})
        result = asyncio.run(generate_pdf_from_state(tc))
        assert result["status"] == "error"
        assert "vazio" in result["message"]

    def test_error_when_key_missing_from_state(self):
        tc = _make_tool_context({})
        result = asyncio.run(generate_pdf_from_state(tc))
        assert result["status"] == "error"

    def test_markdown_with_code_fences_stripped(self):
        fenced_md = "```markdown\n" + SIMPLE_MARKDOWN + "\n```"
        tc = _make_tool_context({"pdf_markdown": fenced_md})
        result = asyncio.run(generate_pdf_from_state(tc))
        assert result["status"] == "success"

    def test_timeout_returns_error(self):
        tc = _make_tool_context({"pdf_markdown": SIMPLE_MARKDOWN})
        with patch(
            "document_processor.tools.generate_artifacts.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = asyncio.run(generate_pdf_from_state(tc))
        assert result["status"] == "error"
        assert "Timeout" in result["message"]

    def test_versao_in_success_message(self):
        tc = _make_tool_context({"pdf_markdown": SIMPLE_MARKDOWN})
        result = asyncio.run(generate_pdf_from_state(tc))
        assert "versão" in result["message"]
