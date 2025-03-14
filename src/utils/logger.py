
"""
Logging utilities for Clarinet.

This module provides a unified logging interface for the Clarinet framework,
using loguru for powerful, flexible logging capabilities.

"""
import sys
import inspect
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Union, overload

from loguru import logger as _logger


class InterceptHandler(logging.Handler):
    """
    Logging handler intercepting standard library logs and redirecting to loguru.

    This allows seamless integration with libraries that use the standard logging module.
    """

    def emit(self, record: logging.LogRecord) -> None:
        # Get corresponding loguru level if it exists
        try:
            level = _logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where the logged message originated
        frame, depth = inspect.currentframe(), 0
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        _logger\
            .opt(depth=depth, exception=record.exc_info)\
            .log(level, record.getMessage())


def setup_logging(
    level: str = "INFO",
    format: str | None = None,
    log_to_file: bool = False,
    log_file: Optional[Union[str, Path]] = None,
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
        log_file: Path to log file (defaults to logs/clarinet.log)
        rotation: When to rotate log files (size or time)
        retention: How long to keep log files
        serialize: Whether to serialize logs as JSON (useful for log aggregation)
    """  # noqa: E501
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
    if log_to_file:
        if log_file is None:
            log_dir = Path("logs")
            log_dir.mkdir(exist_ok=True)
            log_file = log_dir / "clarinet.log"

        _logger.add(
            str(log_file),
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


# Configure a basic console logger by default
setup_logging()

# Export loguru's logger as the module's logger
logger = _logger
