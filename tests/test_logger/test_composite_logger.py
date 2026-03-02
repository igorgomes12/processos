"""
Testes unitários para logger/composite_logger.py.
Cobre: CompositeLogger — dispatch, isolamento de falhas.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import MagicMock, call

import pytest

from logger.base import LogEvent, ProcessLogger
from logger.composite_logger import CompositeLogger


def _make_event(event_type: str = "tool_start") -> LogEvent:
    return LogEvent(
        event_type=event_type,
        tool_name="tool_x",
        session_id="sess-1",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _mock_logger() -> MagicMock:
    """Cria um mock com a interface ProcessLogger."""
    m = MagicMock(spec=ProcessLogger)
    return m


class TestCompositeLogger:
    def test_dispatches_tool_start_to_all_loggers(self):
        l1, l2 = _mock_logger(), _mock_logger()
        composite = CompositeLogger([l1, l2])
        event = _make_event("tool_start")
        composite.log_tool_start(event)
        l1.log_tool_start.assert_called_once_with(event)
        l2.log_tool_start.assert_called_once_with(event)

    def test_dispatches_tool_end_to_all_loggers(self):
        l1, l2 = _mock_logger(), _mock_logger()
        composite = CompositeLogger([l1, l2])
        event = _make_event("tool_end")
        composite.log_tool_end(event)
        l1.log_tool_end.assert_called_once_with(event)
        l2.log_tool_end.assert_called_once_with(event)

    def test_dispatches_tool_error_to_all_loggers(self):
        l1, l2 = _mock_logger(), _mock_logger()
        composite = CompositeLogger([l1, l2])
        event = _make_event("tool_error")
        composite.log_tool_error(event)
        l1.log_tool_error.assert_called_once_with(event)
        l2.log_tool_error.assert_called_once_with(event)

    def test_empty_loggers_list_does_not_raise(self):
        composite = CompositeLogger([])
        composite.log_tool_start(_make_event())  # deve ser no-op sem erro

    def test_single_logger(self):
        l1 = _mock_logger()
        composite = CompositeLogger([l1])
        event = _make_event()
        composite.log_tool_start(event)
        l1.log_tool_start.assert_called_once_with(event)

    def test_failing_logger_does_not_stop_others(self, capsys):
        """Se um logger falhar, os demais ainda recebem o evento."""
        l_bad = _mock_logger()
        l_bad.log_tool_start.side_effect = RuntimeError("crash!")
        l_good = _mock_logger()
        composite = CompositeLogger([l_bad, l_good])
        event = _make_event()
        # Não deve lançar exceção
        composite.log_tool_start(event)
        # O segundo logger deve ter sido chamado mesmo com o primeiro falhando
        l_good.log_tool_start.assert_called_once_with(event)

    def test_failing_logger_prints_to_stderr(self, capsys):
        """Erros de logger devem imprimir no stderr."""
        l_bad = _mock_logger()
        l_bad.log_tool_start.side_effect = RuntimeError("crash!")
        composite = CompositeLogger([l_bad])
        composite.log_tool_start(_make_event())
        captured = capsys.readouterr()
        assert "CompositeLogger" in captured.err
        assert "crash!" in captured.err

    def test_stores_loggers_as_list(self):
        l1, l2, l3 = _mock_logger(), _mock_logger(), _mock_logger()
        composite = CompositeLogger([l1, l2, l3])
        assert len(composite._loggers) == 3

    def test_dispatch_order_preserved(self):
        """Os loggers devem ser chamados na mesma ordem em que foram passados."""
        call_order = []
        l1 = MagicMock(spec=ProcessLogger)
        l1.log_tool_start.side_effect = lambda e: call_order.append("l1")
        l2 = MagicMock(spec=ProcessLogger)
        l2.log_tool_start.side_effect = lambda e: call_order.append("l2")
        composite = CompositeLogger([l1, l2])
        composite.log_tool_start(_make_event())
        assert call_order == ["l1", "l2"]
