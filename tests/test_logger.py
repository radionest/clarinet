"""Unit tests for clarinet.utils.logger."""

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import httpx
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
        assert parsed["path"] == str(Path("/tmp/test"))


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


class TestScrubSensitive:
    """Tests for scrub_sensitive function."""

    def test_scrubs_password_assignment(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        assert "***" in scrub_sensitive("password=secret123")
        assert "secret123" not in scrub_sensitive("password=secret123")

    def test_scrubs_quoted_password(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive('"password": "mypass"')
        assert "mypass" not in result
        assert "***" in result

    def test_scrubs_bearer_token(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
        assert "eyJhbGc" not in result
        assert "Bearer ***" in result

    def test_scrubs_basic_auth(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("Basic dXNlcjpwYXNz")
        assert "dXNlcjpwYXNz" not in result

    def test_scrubs_db_url(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("postgresql://user:s3cret@localhost/db")
        assert "s3cret" not in result
        assert "://user:***@localhost" in result

    def test_scrubs_token_field(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("token=abc123def")
        assert "abc123def" not in result

    def test_scrubs_secret_field(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("secret=mysecretvalue")
        assert "mysecretvalue" not in result

    def test_preserves_normal_text(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        text = "Patient uploaded study successfully"
        assert scrub_sensitive(text) == text

    def test_scrubs_single_quoted_python_dict(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("{'password':'x'}")
        assert "x" not in result or result == "{'password':'***'}"
        assert "***" in result

    def test_scrubs_numeric_json_value(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive('{"password":123}')
        assert "123" not in result
        assert "***" in result

    def test_scrubs_compound_key_single_quoted(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("access_token='abc'")
        assert "abc" not in result
        assert "***" in result

    def test_scrubs_hyphenated_key_assignment(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        result = scrub_sensitive("api-key=secret")
        assert "secret" not in result

    def test_scrubs_multiline_traceback(self) -> None:
        from clarinet.utils.logger import scrub_sensitive

        tb = "Traceback:\n  File app.py\n  password=hunter2\n  token=abc"
        result = scrub_sensitive(tb)
        assert "hunter2" not in result
        assert "abc" not in result


class TestLokiSink:
    """Tests for _LokiSink class."""

    def test_sends_post_to_url(self) -> None:
        from clarinet.utils.logger import _LokiSink

        sink = _LokiSink(url="http://localhost:3100/loki/api/v1/push")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()

        buf = StringIO()
        _logger.remove()
        _logger.add(buf, format="{message}", level="DEBUG")

        with patch.object(sink._client, "post", return_value=mock_response) as mock_post:
            _logger.remove()
            _logger.add(sink, format="{message}", level="DEBUG", enqueue=False)
            _logger.info("test loki message")
            _logger.remove()

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args
            payload = json.loads(call_kwargs.kwargs["content"])
            assert "streams" in payload
            stream = payload["streams"][0]
            assert stream["stream"]["app"] == "clarinet"
            assert stream["stream"]["level"] == "info"
            # The log line is JSON inside the value
            log_line = json.loads(stream["values"][0][1])
            assert log_line["msg"] == "test loki message"

    def test_includes_custom_labels(self) -> None:
        from clarinet.utils.logger import _LokiSink

        sink = _LokiSink(
            url="http://localhost:3100/loki/api/v1/push",
            labels={"env": "test", "host": "server1"},
        )
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()

        with patch.object(sink._client, "post", return_value=mock_response) as mock_post:
            _logger.remove()
            _logger.add(sink, format="{message}", level="DEBUG", enqueue=False)
            _logger.info("labels test")
            _logger.remove()

            payload = json.loads(mock_post.call_args.kwargs["content"])
            labels = payload["streams"][0]["stream"]
            assert labels["env"] == "test"
            assert labels["host"] == "server1"
            assert labels["app"] == "clarinet"

    def test_scrubs_sensitive_in_message(self) -> None:
        from clarinet.utils.logger import _LokiSink

        sink = _LokiSink(url="http://localhost:3100/loki/api/v1/push")
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.raise_for_status = MagicMock()

        with patch.object(sink._client, "post", return_value=mock_response) as mock_post:
            _logger.remove()
            _logger.add(sink, format="{message}", level="DEBUG", enqueue=False)
            _logger.info("connecting with password=hunter2")
            _logger.remove()

            payload = json.loads(mock_post.call_args.kwargs["content"])
            log_line = json.loads(payload["streams"][0]["values"][0][1])
            assert "hunter2" not in log_line["msg"]
            assert "***" in log_line["msg"]

    def test_error_does_not_raise(self) -> None:
        from clarinet.utils.logger import _LokiSink

        sink = _LokiSink(url="http://localhost:3100/loki/api/v1/push")

        with patch.object(sink._client, "post", side_effect=httpx.ConnectError("refused")):
            _logger.remove()
            _logger.add(sink, format="{message}", level="DEBUG", enqueue=False)
            # Should not raise
            _logger.info("this should not crash")
            _logger.remove()

    def test_auth_header_set(self) -> None:
        from clarinet.utils.logger import _LokiSink

        sink = _LokiSink(
            url="http://localhost:3100/loki/api/v1/push",
            auth="Basic dXNlcjprZXk=",
        )
        assert sink._client.headers["Authorization"] == "Basic dXNlcjprZXk="


class TestSetupRemoteSink:
    """Tests for remote sink integration in setup_logging."""

    def test_no_sink_when_url_none(self) -> None:
        from clarinet.utils.logger import setup_logging

        _logger.remove()
        setup_logging(level="DEBUG", remote_url=None)
        # Only console handler — count handlers
        handlers = _logger._core.handlers
        # Should have exactly 1 handler (console)
        assert len(handlers) == 1
        _logger.remove()

    def test_sink_added_when_url_set(self) -> None:
        from clarinet.utils.logger import setup_logging

        _logger.remove()
        setup_logging(level="DEBUG", remote_url="http://localhost:3100/loki/api/v1/push")
        handlers = _logger._core.handlers
        # Console + remote = 2 handlers
        assert len(handlers) == 2
        _logger.remove()
