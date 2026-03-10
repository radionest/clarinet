"""Unit tests for clarinet.utils.logger."""

import json
import sys
from io import StringIO
from unittest.mock import patch

import pytest
from loguru import logger as _logger


class TestJsonDumps:
    """Tests for _json_dumps helper."""

    def test_serializes_dict(self) -> None:
        from clarinet.utils.logger import _json_dumps

        result = _json_dumps({"a": 1, "b": "hello"})
        parsed = json.loads(result)
        assert parsed == {"a": 1, "b": "hello"}

    def test_compact_output(self) -> None:
        from clarinet.utils.logger import _json_dumps

        result = _json_dumps({"key": "value"})
        # No extra spaces — compact format
        assert " " not in result or result == json.dumps({"key": "value"}, separators=(",", ":"))

    def test_non_serializable_fallback(self) -> None:
        from pathlib import Path

        from clarinet.utils.logger import _json_dumps

        result = _json_dumps({"path": Path("/tmp/test")})
        parsed = json.loads(result)
        assert parsed["path"] == "/tmp/test"


class TestJsonFormat:
    """Tests for _json_format callable."""

    def _capture_json_line(self, message: str, *, exception: bool = False) -> str:
        """Log a message through loguru with _json_format and capture output."""
        from clarinet.utils.logger import _json_format

        buf = StringIO()
        _logger.remove()
        _logger.add(buf, format=_json_format, level="DEBUG")

        if exception:
            try:
                raise ValueError("test error")
            except ValueError:
                _logger.exception(message)
        else:
            _logger.info(message)

        _logger.remove()
        return buf.getvalue().strip()

    def test_valid_json_line(self) -> None:
        line = self._capture_json_line("hello world")
        parsed = json.loads(line)
        assert parsed["msg"] == "hello world"
        assert parsed["l"] == "INFO"
        assert "t" in parsed
        assert "mod" in parsed
        assert "fn" in parsed
        assert "line" in parsed

    def test_exception_in_exc_key(self) -> None:
        line = self._capture_json_line("boom", exception=True)
        parsed = json.loads(line)
        assert "exc" in parsed
        assert "ValueError" in parsed["exc"]
        assert "test error" in parsed["exc"]

    def test_no_exc_without_error(self) -> None:
        line = self._capture_json_line("ok")
        parsed = json.loads(line)
        assert "exc" not in parsed

    def test_curly_braces_in_message(self) -> None:
        line = self._capture_json_line("data: {foo}")
        parsed = json.loads(line)
        assert "{foo}" in parsed["msg"]


class TestConsoleFormat:
    """Tests for _CONSOLE_FORMAT string."""

    def test_no_extra_whitespace(self) -> None:
        from clarinet.utils.logger import _CONSOLE_FORMAT

        # The old format had ~19 spaces between fields due to backslash continuation
        assert "                  " not in _CONSOLE_FORMAT

    def test_contains_required_fields(self) -> None:
        from clarinet.utils.logger import _CONSOLE_FORMAT

        assert "{time:" in _CONSOLE_FORMAT
        assert "{level:" in _CONSOLE_FORMAT
        assert "{name}" in _CONSOLE_FORMAT
        assert "{function}" in _CONSOLE_FORMAT
        assert "{line}" in _CONSOLE_FORMAT
        assert "{message}" in _CONSOLE_FORMAT


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_serialize_true_produces_json(self, tmp_path: object) -> None:
        from pathlib import Path

        from clarinet.utils.logger import setup_logging

        tmp = Path(str(tmp_path))
        log_file = tmp / "test.log"

        setup_logging(
            level="DEBUG",
            log_to_file=True,
            log_file=log_file,
            serialize=True,
        )
        _logger.info("json test")
        _logger.remove()

        lines = log_file.read_text().strip().splitlines()
        assert len(lines) >= 1
        parsed = json.loads(lines[-1])
        assert parsed["msg"] == "json test"

    def test_serialize_false_produces_text(self, tmp_path: object) -> None:
        from pathlib import Path

        from clarinet.utils.logger import setup_logging

        tmp = Path(str(tmp_path))
        log_file = tmp / "test.log"

        setup_logging(
            level="DEBUG",
            log_to_file=True,
            log_file=log_file,
            serialize=False,
        )
        _logger.info("text test")
        _logger.remove()

        content = log_file.read_text()
        assert "text test" in content
        # Should not be valid JSON
        for line in content.strip().splitlines():
            if line.strip():
                with pytest.raises(json.JSONDecodeError):
                    json.loads(line)

    def test_console_uses_custom_format(self) -> None:
        from clarinet.utils.logger import setup_logging

        buf = StringIO()
        custom_fmt = "{level} | {message}"

        _logger.remove()
        with patch.object(sys, "stderr", buf):
            setup_logging(level="DEBUG", format=custom_fmt)
            _logger.info("custom format test")

        _logger.remove()
        output = buf.getvalue()
        assert "INFO" in output
        assert "custom format test" in output
