"""
Logging utilities for Clarinet.

This module provides a unified logging interface for the Clarinet framework,
using loguru for powerful, flexible logging capabilities.

"""

import inspect
import logging
import sys
from pathlib import Path

from loguru import logger as _logger

from ..settings import settings


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
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        _logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


def setup_logging(
    level: str = "INFO",
    format: str | None = None,
    log_to_file: bool = False,
    log_file: str | Path | None = None,
    rotation: str = "20 MB",
    retention: str = "1 week",
    serialize: bool = False,
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Minimum log level to capture
        format: Log message format string
        log_to_file: Whether to log to a file in addition to console
        log_file: Path to log file (will be created if doesn't exist)
        rotation: When to rotate log files (size or time)
        retention: How long to keep log files
        serialize: Whether to serialize logs as JSON (useful for log aggregation)
    """
    # Remove default handlers

    if format is None:
        format = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | \
                  <level>{level: <8}</level> | \
                  <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - \
                  <level>{message}</level>"

    _logger.remove()

    # Add console handler
    _logger.add(
        sys.stderr,
        level=level,
        format=format,
        colorize=True,
        backtrace=True,
        diagnose=True,
    )

    # Add file handler if requested
    if log_to_file and log_file:
        # Ensure the parent directory exists
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        _logger.add(
            str(log_path),
            level=level,
            format=format,
            rotation=rotation,
            retention=retention,
            compression="zip",
            serialize=serialize,
            backtrace=True,
            diagnose=True,
        )

    # Intercept standard library logging
    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    # Capture common libraries' logs
    for log_name in ["uvicorn", "uvicorn.error", "fastapi", "sqlalchemy"]:
        logging.getLogger(log_name).handlers = [InterceptHandler()]


# Configure logging with settings from config
setup_logging(
    level=settings.log_level,
    format=settings.log_format,
    log_to_file=settings.log_to_file,
    log_file=settings.get_log_dir() / "clarinet.log" if settings.log_to_file else None,
    rotation=settings.log_rotation,
    retention=settings.log_retention,
)

# Export loguru's logger as the module's logger
logger = _logger
