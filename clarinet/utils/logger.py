"""
Logging utilities for Clarinet.

This module provides a unified logging interface for the Clarinet framework,
using loguru for powerful, flexible logging capabilities.

"""

from __future__ import annotations

import inspect
import json
import logging
import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger as _logger

from ..settings import settings

if TYPE_CHECKING:
    from collections.abc import Callable

    from loguru import Record

try:
    import orjson

    def _json_dumps(data: dict) -> str:
        """Serialize dict to compact JSON string using orjson."""
        result: str = orjson.dumps(data, default=str).decode()
        return result

except ImportError:

    def _json_dumps(data: dict) -> str:
        """Serialize dict to compact JSON string using stdlib json."""
        return json.dumps(data, separators=(",", ":"), default=str)


def _json_format(record: Record) -> str:
    """Format a loguru record as a JSON line.

    Stores the serialized JSON in ``record["extra"]["_json"]`` and returns
    a loguru template that references it so curly braces in log messages
    don't conflict with loguru's ``{}`` interpolation.
    """
    subset: dict = {
        "t": record["time"].strftime("%Y-%m-%dT%H:%M:%S.%f%z"),
        "l": record["level"].name,
        "mod": record["name"],
        "fn": record["function"],
        "line": record["line"],
        "msg": record["message"],
    }

    if record["exception"] is not None:
        exc = record["exception"]
        if exc.type is not None:
            subset["exc"] = "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))

    record["extra"]["_json"] = _json_dumps(subset)
    return "{extra[_json]}\n"


_CONSOLE_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


_WARNING_LEVEL_NO = 30


def _make_noisy_library_filter(prefixes: list[str]) -> Callable[[Record], bool]:
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
        noisy_libraries: Library name prefixes to suppress on console (DEBUG/INFO hidden,
            WARNING+ still shown). Pass empty list to disable filtering.
    """
    console_format = format or _CONSOLE_FORMAT
    console_filter = _make_noisy_library_filter(noisy_libraries) if noisy_libraries else None

    _logger.remove()

    # Add console handler (enqueue for thread safety — background threads
    # from pynetdicom et al. may outlive the main thread / pytest teardown)
    _logger.add(
        sys.stderr,
        level=console_level or level,
        format=console_format,
        filter=console_filter,
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
            rotation=rotation,
            retention=retention,
            compression="zip",
            backtrace=True,
            diagnose=not serialize,
            enqueue=True,
        )

    # Intercept standard library logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)


def reconfigure_for_worker() -> None:
    """Re-initialize logging to write to clarinet_worker.log.

    This re-calls :func:`setup_logging` using the same settings but
    directing file logs to ``clarinet_worker.log`` when file logging is
    enabled. It is intended to be called by the pipeline worker process
    early during startup so worker logs are separated from the API server.
    """
    setup_logging(
        level=settings.log_level,
        console_level=settings.log_console_level,
        format=settings.log_format,
        log_to_file=settings.log_to_file,
        log_file=settings.get_log_dir() / "clarinet_worker.log" if settings.log_to_file else None,
        rotation=settings.log_rotation,
        retention=settings.log_retention,
        serialize=settings.log_serialize,
        noisy_libraries=settings.log_noisy_libraries,
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
)

# Export loguru's logger as the module's logger
logger = _logger
