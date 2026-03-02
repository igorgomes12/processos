"""
Testes unitários para logger/base.py.
Cobre: LogEvent, _truncate, summarize_args.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from logger.base import LogEvent, _truncate, summarize_args


# ─── LogEvent ────────────────────────────────────────────────────────────────

class TestLogEvent:
    def test_default_timestamp_is_utc(self):
        event = LogEvent(event_type="tool_start", tool_name="my_tool", session_id="s1")
        assert event.timestamp.tzinfo is not None

    def test_default_duration_is_none(self):
        event = LogEvent(event_type="tool_start", tool_name="my_tool", session_id="s1")
        assert event.duration_ms is None

    def test_default_args_summary_is_empty(self):
        event = LogEvent(event_type="tool_start", tool_name="my_tool", session_id="s1")
        assert event.args_summary == {}

    def test_default_result_summary_is_none(self):
        event = LogEvent(event_type="tool_start", tool_name="my_tool", session_id="s1")
        assert event.result_summary is None

    def test_default_error_msg_is_none(self):
        event = LogEvent(event_type="tool_start", tool_name="my_tool", session_id="s1")
        assert event.error_msg is None

    def test_to_dict_keys(self):
        event = LogEvent(event_type="tool_end", tool_name="gen_xlsx", session_id="abc")
        d = event.to_dict()
        assert set(d.keys()) == {
            "event_type", "tool_name", "session_id", "timestamp",
            "duration_ms", "args_summary", "result_summary", "error_msg",
        }

    def test_to_dict_values_match(self):
        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        event = LogEvent(
            event_type="tool_error",
            tool_name="save_firestore",
            session_id="sess-42",
            timestamp=ts,
            duration_ms=123.45,
            args_summary={"json_str": "some"},
            result_summary=None,
            error_msg="Connection refused",
        )
        d = event.to_dict()
        assert d["event_type"] == "tool_error"
        assert d["tool_name"] == "save_firestore"
        assert d["session_id"] == "sess-42"
        assert d["timestamp"] == ts.isoformat()
        assert d["duration_ms"] == 123.45
        assert d["args_summary"] == {"json_str": "some"}
        assert d["error_msg"] == "Connection refused"

    def test_to_dict_timestamp_is_isoformat_string(self):
        event = LogEvent(event_type="tool_start", tool_name="t", session_id="s")
        d = event.to_dict()
        # must be parseable as ISO datetime
        parsed = datetime.fromisoformat(d["timestamp"])
        assert isinstance(parsed, datetime)

    def test_to_dict_result_summary_is_truncated_when_long(self):
        long_value = "x" * 600
        event = LogEvent(
            event_type="tool_end",
            tool_name="t",
            session_id="s",
            result_summary=long_value,
        )
        d = event.to_dict()
        # should be truncated (500 chars + truncation marker)
        assert len(d["result_summary"]) < len(long_value)
        assert "truncado" in d["result_summary"]


# ─── _truncate ───────────────────────────────────────────────────────────────

class TestTruncate:
    def test_none_returns_none(self):
        assert _truncate(None) is None

    def test_short_string_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_long_string_is_truncated(self):
        long_str = "a" * 600
        result = _truncate(long_str)
        assert len(result) < 600
        assert "truncado" in result

    def test_truncation_at_exact_500(self):
        s = "a" * 500
        result = _truncate(s)
        # exactly 500 chars should NOT be truncated
        assert result == s

    def test_truncation_at_501(self):
        s = "a" * 501
        result = _truncate(s)
        assert "truncado" in result

    def test_non_string_value_converted(self):
        result = _truncate(12345)
        assert result == "12345"

    def test_list_converted(self):
        result = _truncate([1, 2, 3])
        assert isinstance(result, str)

    def test_custom_max_len(self):
        result = _truncate("hello world", max_len=5)
        assert "truncado" in result

    def test_empty_string(self):
        assert _truncate("") == ""


# ─── summarize_args ──────────────────────────────────────────────────────────

class TestSummarizeArgs:
    def test_empty_dict(self):
        assert summarize_args({}) == {}

    def test_none_dict(self):
        result = summarize_args(None)
        assert result == {}

    def test_string_values_short(self):
        result = summarize_args({"key": "value"})
        assert result == {"key": "value"}

    def test_long_value_truncated(self):
        result = summarize_args({"data": "x" * 600})
        assert "truncado" in result["data"]

    def test_multiple_keys(self):
        result = summarize_args({"a": "1", "b": "2", "c": "3"})
        assert set(result.keys()) == {"a", "b", "c"}

    def test_non_string_values_converted(self):
        result = summarize_args({"num": 42, "lst": [1, 2, 3]})
        assert result["num"] == "42"
        assert isinstance(result["lst"], str)
