"""
Logging utilities for Clarinet.

This module provides a unified logging interface for the Clarinet framework,
using loguru for powerful, flexible logging capabilities.

"""

from __future__ import annotations

import inspect
import json
import logging
import re
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from loguru import logger as _logger

from ..settings import settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from loguru import Record

try:
    import orjson

    def _json_dumps(data: dict[str, Any]) -> str:
        """Serialize dict to compact JSON string using orjson."""
        result: str = orjson.dumps(data, default=str).decode()
        return result

except ImportError:

    def _json_dumps(data: dict[str, Any]) -> str:
        """Serialize dict to compact JSON string using stdlib json."""
        return json.dumps(data, separators=(",", ":"), default=str)


_SENSITIVE_KEY = r"(?:password|token|secret|api[_-]?key|auth|credentials?|private[_-]?key)"
_SCRUB_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # "key": "value" or 'key': 'value' (JSON / Python repr with quoted keys)
    (re.compile(rf"""(['"]?{_SENSITIVE_KEY}['"]?\s*:\s*)'([^']+)'""", re.IGNORECASE), r"\1'***'"),
    (re.compile(rf"""(['"]?{_SENSITIVE_KEY}['"]?\s*:\s*)"([^"]+)\"""", re.IGNORECASE), r'\1"***"'),
    # "key": 123 or 'key': 123 (unquoted / numeric values after colon)
    (
        re.compile(rf"""(['"]?{_SENSITIVE_KEY}['"]?\s*:\s*)([^\s,}}'\"]+)""", re.IGNORECASE),
        r"\1***",
    ),
    # key='value' or key="value" (assignment with quotes)
    (re.compile(rf"({_SENSITIVE_KEY}\s*=\s*)'([^']+)'", re.IGNORECASE), r"\1'***'"),
    (re.compile(rf'({_SENSITIVE_KEY}\s*=\s*)"([^"]+)"', re.IGNORECASE), r'\1"***"'),
    # key=value (plain text, unquoted)
    (re.compile(rf"({_SENSITIVE_KEY}\s*=\s*)[^\s,}}\"']+", re.IGNORECASE), r"\1***"),
    # Bearer/Basic tokens
    (re.compile(r"((?:Bearer|Basic)\s+)\S+", re.IGNORECASE), r"\1***"),
    # DB URLs: driver://user:password@host
    (re.compile(r"(://[^:]+:)[^@]+(@)"), r"\1***\2"),
]


def scrub_sensitive(text: str) -> str:
    """Remove passwords, tokens, and credentials from a log string."""
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _scrub_patcher(record: Record) -> None:
    """Loguru patcher that scrubs secrets from every record's message.

    Applied globally via ``_logger.patch`` so console, file, and remote sinks
    all see the sanitized text. Exception tracebacks are still scrubbed
    explicitly in ``_json_format`` and ``_LokiSink`` because the patcher only
    runs at record creation time, while exceptions are formatted later.
    """
    record["message"] = scrub_sensitive(record["message"])


class _LokiSink:
    """Loguru sink that POSTs JSON logs to a Loki-compatible endpoint."""

    def __init__(
        self,
        url: str,
        auth: str | None = None,
        labels: dict[str, str] | None = None,
    ) -> None:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = auth
        self._client = httpx.Client(timeout=5.0, headers=headers)
        self._url = url
        self._labels = {"app": "clarinet", **(labels or {})}

    def __call__(self, message: Any) -> None:
        record: Record = message.record
        subset: dict[str, Any] = {
            "t": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
            "l": record["level"].name,
            "mod": record["name"],
            "fn": record["function"],
            "line": record["line"],
            "msg": scrub_sensitive(record["message"]),
        }

        if record["exception"] is not None:
            exc = record["exception"]
            if exc.type is not None:
                subset["exc"] = scrub_sensitive(
                    "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
                )

        log_line = _json_dumps(subset)
        ts_ns = str(int(record["time"].timestamp() * 1_000_000_000))

        payload = _json_dumps(
            {
                "streams": [
                    {
                        "stream": {**self._labels, "level": record["level"].name.lower()},
                        "values": [[ts_ns, log_line]],
                    }
                ]
            }
        )

        try:
            resp = self._client.post(self._url, content=payload)
            resp.raise_for_status()
        except Exception:
            # Avoid recursion — write to stderr, not logger
            print(f"[clarinet] remote log sink error: {self._url}", file=sys.stderr)


def _json_format(record: Record) -> str:
    """Format a loguru record as a JSON line.

    Stores the serialized JSON in ``record["extra"]["_json"]`` and returns
    a loguru template that references it so curly braces in log messages
    don't conflict with loguru's ``{}`` interpolation.
    """
    subset: dict[str, Any] = {
        "t": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
        "l": record["level"].name,
        "mod": record["name"],
        "fn": record["function"],
        "line": record["line"],
        "msg": scrub_sensitive(record["message"]),
    }

    if record["exception"] is not None:
        exc = record["exception"]
        if exc.type is not None:
            subset["exc"] = scrub_sensitive(
                "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
            )

    record["extra"]["_json"] = _json_dumps(subset)
    return "{extra[_json]}\n"


_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


_WARNING_LEVEL_NO = 30


def _make_noisy_filter(prefixes: list[str]) -> Callable[[Record], bool]:
    """Create a loguru filter that suppresses DEBUG/INFO from noisy libraries.

    Args:
        prefixes: Library name prefixes to suppress (e.g. ["pynetdicom"]).
            Empty list disables filtering.
    """
    prefix_tuple = tuple(prefixes)

    def _filter(record: Record) -> bool:
        name = record["name"]
        if name and name.startswith(prefix_tuple):
            return record["level"].no >= _WARNING_LEVEL_NO
        return True

    return _filter


class InterceptHandler(logging.Handler):
    """
    Logging handler intercepting standard library logs and redirecting to loguru.

    This allows seamless integration with libraries that use the standard logging module.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding loguru level if it exists
        level: str | int
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where the logged message originated
        frame, depth = inspect.currentframe(), 0
        while frame:
            filename = frame.f_code.co_filename
            is_logging = filename == logging.__file__
            is_frozen = "importlib" in filename and "_bootstrap" in filename
            if depth > 0 and not (is_logging or is_frozen):
                break
            frame = frame.f_back
            depth += 1

        _logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(
    level: str = "INFO",
    console_level: str | None = None,
    format: str | None = None,
    log_to_file: bool = False,
    log_file: str | Path | None = None,
    rotation: str = "20 MB",
    retention: str = "1 week",
    serialize: bool = False,
    noisy_libraries: list[str] | None = None,
    remote_url: str | None = None,
    remote_auth: str | None = None,
    remote_level: str | None = None,
    remote_labels: dict[str, str] | None = None,
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Minimum log level to capture
        console_level: Minimum log level for console output (defaults to level)
        format: Log message format string
        log_to_file: Whether to log to a file in addition to console
        log_file: Path to log file (will be created if doesn't exist)
        rotation: When to rotate log files (size or time)
        retention: How long to keep log files
        serialize: Whether to format file logs as JSON lines
        noisy_libraries: Library name prefixes to suppress on console and file sinks
            (DEBUG/INFO hidden, WARNING+ still shown). Pass empty list to disable.
        remote_url: Loki-compatible push endpoint URL. None disables remote logging.
        remote_auth: Authorization header value for the remote endpoint.
        remote_level: Minimum level for remote sink (defaults to level).
        remote_labels: Extra Loki stream labels (e.g. {"env": "prod"}).
    """
    console_format = format or _CONSOLE_FORMAT
    noisy_filter = _make_noisy_filter(noisy_libraries) if noisy_libraries else None

    _logger.remove()

    # Add console handler (enqueue for thread safety — background threads
    # from pynetdicom et al. may outlive the main thread / pytest teardown)
    _logger.add(
        sys.stderr,
        level=console_level or level,
        format=console_format,
        filter=noisy_filter,
        colorize=True,
        backtrace=True,
        diagnose=True,
        enqueue=True,
    )

    # Add file handler if requested
    if log_to_file and log_file:
        # Ensure the parent directory exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_format = _json_format if serialize else console_format

        _logger.add(
            str(log_path),
            level=level,
            format=file_format,
            filter=noisy_filter,
            rotation=rotation,
            retention=retention,
            compression="zip",
            backtrace=True,
            diagnose=not serialize,
            enqueue=True,
        )

    # Add remote Loki sink if configured
    if remote_url:
        sink = _LokiSink(url=remote_url, auth=remote_auth, labels=remote_labels)
        _logger.add(
            sink,
            level=remote_level or level,
            format="{message}",
            backtrace=True,
            diagnose=False,
            enqueue=True,
        )

    # Intercept standard library logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)


def reconfigure_for_worker(log_file: str | None = None) -> None:
    """Re-initialize logging for the pipeline worker.

    Re-calls :func:`setup_logging` directing file logs to a worker-specific
    file when file logging is enabled. Intended to be called by the worker
    process early during startup so worker logs are separated from the API
    server.

    Args:
        log_file: Optional override for the worker log path. If ``None``,
            falls back to ``settings.worker_log_file``, then to the default
            ``clarinet_worker.log``. Resolution rules: see
            :meth:`Settings.get_worker_log_file`.
    """
    setup_logging(
        level=settings.log_level,
        console_level=settings.log_console_level,
        format=settings.log_format,
        log_to_file=settings.log_to_file,
        log_file=settings.get_worker_log_file(log_file),
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        serialize=settings.log_serialize,
        noisy_libraries=settings.log_noisy_libraries,
        remote_url=settings.log_remote_url,
        remote_auth=settings.log_remote_auth,
        remote_level=settings.log_remote_level,
        remote_labels={**settings.log_remote_labels, "component": "worker"},
    )


# Configure logging with settings from config
setup_logging(
    level=settings.log_level,
    console_level=settings.log_console_level,
    format=settings.log_format,
    log_to_file=settings.log_to_file,
    log_file=settings.get_log_dir() / "clarinet.log" if settings.log_to_file else None,
    rotation=settings.log_rotation,
    retention=settings.log_retention,
    serialize=settings.log_serialize,
    noisy_libraries=settings.log_noisy_libraries,
    remote_url=settings.log_remote_url,
    remote_auth=settings.log_remote_auth,
    remote_level=settings.log_remote_level,
    remote_labels={**settings.log_remote_labels, "component": "api"},
)

# Apply the scrub patcher so every log call (regardless of sink) gets a
# sanitized message — defense in depth on top of explicit scrubbing in
# `_json_format` and `_LokiSink`. Reassigning `_logger` ensures
# `InterceptHandler.emit` (which redirects stdlib logging from sqlalchemy,
# fastapi-users, etc.) also goes through the patched proxy.
_logger = _logger.patch(_scrub_patcher)

# Export loguru's logger as the module's logger
logger = _logger
