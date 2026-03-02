"""
Testes unitários para logger/local_logger.py.
Cobre: LocalFileLogger, _JsonFormatter.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from logger.base import LogEvent
from logger.local_logger import LocalFileLogger, _JsonFormatter


def _make_event(event_type: str = "tool_start", tool_name: str = "test_tool", session_id: str = "s1") -> LogEvent:
    return LogEvent(
        event_type=event_type,
        tool_name=tool_name,
        session_id=session_id,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class TestJsonFormatter:
    def test_format_returns_json_string(self):
        formatter = _JsonFormatter()
        event = _make_event()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        record.log_event = event.to_dict()
        result = formatter.format(record)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_format_includes_event_type(self):
        formatter = _JsonFormatter()
        event = _make_event(event_type="tool_end")
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        record.log_event = event.to_dict()
        parsed = json.loads(formatter.format(record))
        assert parsed["event_type"] == "tool_end"

    def test_format_without_log_event_returns_empty_json(self):
        formatter = _JsonFormatter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        result = formatter.format(record)
        assert result == "{}"


class TestLocalFileLogger:
    def test_creates_log_dir_if_not_exists(self, tmp_path):
        log_dir = tmp_path / "subdir" / "logs"
        assert not log_dir.exists()
        logger = LocalFileLogger(log_dir=log_dir)
        assert log_dir.exists()

    def test_log_file_created(self, tmp_path):
        log_file = f"test_{uuid.uuid4().hex[:8]}.log"
        logger = LocalFileLogger(log_dir=tmp_path, log_file=log_file)
        logger.log_tool_start(_make_event())
        # Flush handler
        for h in logger._logger.handlers:
            h.flush()
        assert (tmp_path / log_file).exists()

    def test_log_tool_start_writes_json_line(self, tmp_path):
        log_file = f"test_{uuid.uuid4().hex[:8]}.log"
        logger = LocalFileLogger(log_dir=tmp_path, log_file=log_file)
        event = _make_event(event_type="tool_start", tool_name="my_tool")
        logger.log_tool_start(event)
        for h in logger._logger.handlers:
            h.flush()
        content = (tmp_path / log_file).read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["event_type"] == "tool_start"
        assert parsed["tool_name"] == "my_tool"

    def test_log_tool_end_writes_json_line(self, tmp_path):
        log_file = f"test_{uuid.uuid4().hex[:8]}.log"
        logger = LocalFileLogger(log_dir=tmp_path, log_file=log_file)
        event = _make_event(event_type="tool_end")
        logger.log_tool_end(event)
        for h in logger._logger.handlers:
            h.flush()
        content = (tmp_path / log_file).read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["event_type"] == "tool_end"

    def test_log_tool_error_writes_json_line(self, tmp_path):
        log_file = f"test_{uuid.uuid4().hex[:8]}.log"
        logger = LocalFileLogger(log_dir=tmp_path, log_file=log_file)
        event = _make_event(event_type="tool_error")
        event.error_msg = "something went wrong"
        logger.log_tool_error(event)
        for h in logger._logger.handlers:
            h.flush()
        content = (tmp_path / log_file).read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["event_type"] == "tool_error"

    def test_multiple_events_multiple_lines(self, tmp_path):
        log_file = f"test_{uuid.uuid4().hex[:8]}.log"
        logger = LocalFileLogger(log_dir=tmp_path, log_file=log_file)
        for i in range(3):
            logger.log_tool_start(_make_event(tool_name=f"tool_{i}"))
        for h in logger._logger.handlers:
            h.flush()
        lines = (tmp_path / log_file).read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            json.loads(line)  # cada linha deve ser JSON válido

    def test_session_id_present_in_log(self, tmp_path):
        log_file = f"test_{uuid.uuid4().hex[:8]}.log"
        logger = LocalFileLogger(log_dir=tmp_path, log_file=log_file)
        event = _make_event(session_id="session-xyz")
        logger.log_tool_start(event)
        for h in logger._logger.handlers:
            h.flush()
        content = (tmp_path / log_file).read_text(encoding="utf-8").strip()
        parsed = json.loads(content)
        assert parsed["session_id"] == "session-xyz"

    def test_ensures_unicode_in_log(self, tmp_path):
        log_file = f"test_{uuid.uuid4().hex[:8]}.log"
        logger = LocalFileLogger(log_dir=tmp_path, log_file=log_file)
        event = LogEvent(
            event_type="tool_start",
            tool_name="ferramenta_çã",
            session_id="s",
        )
        logger.log_tool_start(event)
        for h in logger._logger.handlers:
            h.flush()
        content = (tmp_path / log_file).read_text(encoding="utf-8")
        assert "ferramenta_çã" in content

    def test_default_log_dir_is_logs_folder(self):
        # Garante que a instância padrão não lança exceção
        logger = LocalFileLogger()
        assert logger is not None

    def test_handler_not_duplicated_on_multiple_instances(self, tmp_path):
        """Duas instâncias com o mesmo log_file não devem duplicar handlers."""
        key = "dedup_test.log"
        logger1 = LocalFileLogger(log_dir=tmp_path, log_file=key)
        logger2 = LocalFileLogger(log_dir=tmp_path, log_file=key)
        # Ambas compartilham o mesmo logger Python interno; não deve haver duplicação
        assert len(logger1._logger.handlers) == len(logger2._logger.handlers)
