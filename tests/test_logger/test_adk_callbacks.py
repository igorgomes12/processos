"""
Testes unitários para logger/adk_callbacks.py.
Cobre: make_before_tool_callback, make_after_tool_callback, _get_tool_name, _get_session_id.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from logger.adk_callbacks import (
    make_before_tool_callback,
    make_after_tool_callback,
    _get_tool_name,
    _get_session_id,
)
from logger.base import LogEvent, ProcessLogger


# ─── helpers ─────────────────────────────────────────────────────────────────

def _mock_logger() -> MagicMock:
    m = MagicMock(spec=ProcessLogger)
    return m


def _make_tool(name: str = "my_tool"):
    tool = MagicMock()
    tool.name = name
    return tool


def _make_tool_context(session_id: str = "sess-123"):
    tc = MagicMock()
    tc.invocation_context.session.id = session_id
    return tc


# ─── _get_tool_name ──────────────────────────────────────────────────────────

class TestGetToolName:
    def test_returns_tool_name_attribute(self):
        tool = _make_tool("xlsx_generator")
        assert _get_tool_name(tool) == "xlsx_generator"

    def test_returns_class_name_when_no_name(self):
        tool = object()  # sem atributo 'name'
        result = _get_tool_name(tool)
        assert result == "object"

    def test_returns_class_name_when_name_is_none(self):
        tool = MagicMock()
        tool.name = None
        result = _get_tool_name(tool)
        # deve retornar o type name quando name é falsy
        assert isinstance(result, str)
        assert len(result) > 0


# ─── _get_session_id ─────────────────────────────────────────────────────────

class TestGetSessionId:
    def test_extracts_from_invocation_context_session_id(self):
        tc = _make_tool_context("abc-123")
        result = _get_session_id(tc)
        assert result == "abc-123"

    def test_falls_back_to_session_id_attribute(self):
        tc = MagicMock()
        del tc.invocation_context  # remove o caminho principal
        tc.session_id = "fallback-id"
        result = _get_session_id(tc)
        assert result == "fallback-id"

    def test_returns_unknown_when_all_fail(self):
        tc = object()  # sem nenhum atributo relevante
        result = _get_session_id(tc)
        assert result == "unknown"

    def test_returns_unknown_on_none_context(self):
        result = _get_session_id(None)
        assert result == "unknown"

    def test_empty_session_id_falls_through(self):
        tc = MagicMock()
        tc.invocation_context.session.id = ""
        tc.invocation_context.session_id = ""
        tc.session_id = "real-id"
        result = _get_session_id(tc)
        assert result == "real-id"


# ─── make_before_tool_callback ───────────────────────────────────────────────

class TestMakeBeforeToolCallback:
    def test_returns_callable(self):
        logger = _mock_logger()
        cb = make_before_tool_callback(logger)
        assert callable(cb)

    def test_calls_log_tool_start(self):
        logger = _mock_logger()
        cb = make_before_tool_callback(logger)
        tool = _make_tool("gen_xlsx")
        tc = _make_tool_context("s1")
        result = cb(tool, {"arg": "val"}, tc)
        logger.log_tool_start.assert_called_once()
        event = logger.log_tool_start.call_args[0][0]
        assert isinstance(event, LogEvent)
        assert event.tool_name == "gen_xlsx"
        assert event.event_type == "tool_start"

    def test_returns_none(self):
        logger = _mock_logger()
        cb = make_before_tool_callback(logger)
        tool = _make_tool()
        tc = _make_tool_context()
        result = cb(tool, {}, tc)
        assert result is None

    def test_stores_start_time(self):
        logger = _mock_logger()
        cb = make_before_tool_callback(logger)
        tool = _make_tool("t1")
        tc = _make_tool_context("s1")
        cb(tool, {}, tc)
        key = ("s1", "t1")
        assert key in cb._start_times

    def test_logger_exception_does_not_propagate(self):
        logger = _mock_logger()
        logger.log_tool_start.side_effect = RuntimeError("logger crash")
        cb = make_before_tool_callback(logger)
        tool = _make_tool()
        tc = _make_tool_context()
        # não deve propagar exceção
        cb(tool, {}, tc)

    def test_args_summary_included_in_event(self):
        logger = _mock_logger()
        cb = make_before_tool_callback(logger)
        tool = _make_tool()
        tc = _make_tool_context()
        cb(tool, {"param": "value"}, tc)
        event = logger.log_tool_start.call_args[0][0]
        assert "param" in event.args_summary


# ─── make_after_tool_callback ────────────────────────────────────────────────

class TestMakeAfterToolCallback:
    def test_returns_callable(self):
        logger = _mock_logger()
        cb = make_after_tool_callback(logger)
        assert callable(cb)

    def test_calls_log_tool_end(self):
        logger = _mock_logger()
        cb_after = make_after_tool_callback(logger)
        tool = _make_tool("pdf_tool")
        tc = _make_tool_context("s1")
        cb_after(tool, {}, tc, {"status": "success"})
        logger.log_tool_end.assert_called_once()
        event = logger.log_tool_end.call_args[0][0]
        assert event.event_type == "tool_end"
        assert event.tool_name == "pdf_tool"

    def test_returns_none(self):
        logger = _mock_logger()
        cb_after = make_after_tool_callback(logger)
        tool = _make_tool()
        tc = _make_tool_context()
        result = cb_after(tool, {}, tc, {"status": "ok"})
        assert result is None

    def test_calculates_duration_ms(self):
        logger = _mock_logger()
        cb_before = make_before_tool_callback(logger)
        cb_after = make_after_tool_callback(logger, before_callback=cb_before)

        tool = _make_tool("duration_tool")
        tc = _make_tool_context("s1")
        cb_before(tool, {}, tc)
        time.sleep(0.01)  # garante que há alguma duração mensurável
        cb_after(tool, {}, tc, {})

        event = logger.log_tool_end.call_args[0][0]
        assert event.duration_ms is not None
        assert event.duration_ms > 0

    def test_duration_none_when_no_before_callback(self):
        logger = _mock_logger()
        cb_after = make_after_tool_callback(logger, before_callback=None)
        tool = _make_tool()
        tc = _make_tool_context()
        cb_after(tool, {}, tc, {})
        event = logger.log_tool_end.call_args[0][0]
        assert event.duration_ms is None

    def test_duration_none_when_before_not_called(self):
        logger = _mock_logger()
        cb_before = make_before_tool_callback(logger)
        cb_after = make_after_tool_callback(logger, before_callback=cb_before)
        # Não chamamos cb_before — key não existe em _start_times
        tool = _make_tool("missing_key")
        tc = _make_tool_context("s1")
        cb_after(tool, {}, tc, {})
        event = logger.log_tool_end.call_args[0][0]
        assert event.duration_ms is None

    def test_logger_exception_does_not_propagate(self):
        logger = _mock_logger()
        logger.log_tool_end.side_effect = RuntimeError("crash")
        cb_after = make_after_tool_callback(logger)
        tool = _make_tool()
        tc = _make_tool_context()
        cb_after(tool, {}, tc, {})  # não deve lançar

    def test_start_time_removed_after_after_callback(self):
        logger = _mock_logger()
        cb_before = make_before_tool_callback(logger)
        cb_after = make_after_tool_callback(logger, before_callback=cb_before)
        tool = _make_tool("cleanup_tool")
        tc = _make_tool_context("s1")
        cb_before(tool, {}, tc)
        key = ("s1", "cleanup_tool")
        assert key in cb_before._start_times
        cb_after(tool, {}, tc, {})
        assert key not in cb_before._start_times
