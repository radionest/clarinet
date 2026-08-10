"""Regression tests for the dedicated ``UnsafePathError`` exception handler.

``UnsafePathError`` deliberately keeps the offending substituted value out of
``str(exc)`` -- it travels only via ``exc.value`` / ``exc.metadata()`` (see the
class's PII-guard docstring in ``clarinet/exceptions/domain.py``). Without an
explicit handler, ``UnsafePathError`` (a ``ConfigurationError`` subclass) fell
through to ``handle_configuration_error``, which calls
``logger.opt(exception=exc).error(...)``. That attaches a traceback, and the
console/file sinks configured with ``diagnose=True`` (``clarinet/utils/logger.py``)
render **frame locals** into that traceback -- including ``exc.value`` -- so the
guarded value reached stderr on every deployment regardless of the PII guard.

These tests capture loguru's fully *rendered* text (not just the structured
record dict used by ``test_exception_handler_logging.py``) because the leak
only manifests at render time: ``record["message"]`` never contains the
traceback block, so a record-dict-only sink cannot prove or disprove it.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from clarinet.api.exception_handlers import setup_exception_handlers
from clarinet.exceptions.domain import ConfigurationError, UnsafePathError
from clarinet.utils.logger import logger

# Obviously-synthetic marker -- never a plausible patient identifier or
# clinical string, so it can't be mistaken for real PHI in test output.
FIXTURE_VALUE = "TESTFIXTURE_traversal_value_not_pii"
MESSAGE = "rendered name escapes the working dir /fake/base/dir (test fixture)"


@pytest.fixture
def captured_console_output():
    """Capture loguru's rendered text through a sink configured like the real
    console/file sinks (``diagnose=True``, ``backtrace=True``) -- this is what
    actually renders exception frame locals into the output text.
    """
    lines: list[str] = []
    sink_id = logger.add(
        lambda msg: lines.append(str(msg)),
        level="DEBUG",
        diagnose=True,
        backtrace=True,
        colorize=False,
    )
    yield lines
    logger.remove(sink_id)


@pytest.fixture
def captured_records():
    """Capture every loguru record emitted during the test as raw dicts.

    Mirrors the idiom in ``tests/test_exception_handler_logging.py`` --
    used here only to inspect ``record["exception"]``, not message text.
    """
    records: list[dict] = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    yield records
    logger.remove(sink_id)


@pytest_asyncio.fixture
async def client():
    """Tiny FastAPI app with one route raising ``UnsafePathError`` and one
    raising a plain ``ConfigurationError`` (not ``UnsafePathError``), so the
    generic handler's continued behavior can be verified in the same app.
    """
    app = FastAPI()
    setup_exception_handlers(app)

    @app.get("/trigger/unsafe-path")
    async def _unsafe_path() -> None:
        raise UnsafePathError(MESSAGE, value=FIXTURE_VALUE)

    @app.get("/trigger/configuration-error")
    async def _configuration_error() -> None:
        raise ConfigurationError("plain configuration error (test fixture)")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_unsafe_path_error_log_omits_the_value(
    client: AsyncClient, captured_console_output: list[str]
) -> None:
    """The rendered log output must carry the message but never ``exc.value``.

    This is the actual PHI leak the dedicated handler exists to close: before
    the fix, this assertion fails because ``diagnose=True`` frame-locals
    rendering puts ``FIXTURE_VALUE`` in the traceback block.
    """
    response = await client.get("/trigger/unsafe-path")
    assert response.status_code == 500

    output = "".join(captured_console_output)
    assert MESSAGE in output
    assert FIXTURE_VALUE not in output


@pytest.mark.asyncio
async def test_unsafe_path_error_does_not_attach_a_traceback(
    client: AsyncClient, captured_records: list[dict]
) -> None:
    """No ``logger.opt(exception=...)`` -- so no traceback is ever rendered,
    which is *why* the value can't leak (frame-locals rendering only happens
    for records carrying exception info)."""
    response = await client.get("/trigger/unsafe-path")
    assert response.status_code == 500

    matches = [r for r in captured_records if MESSAGE in r["message"]]
    assert len(matches) == 1, f"expected one ERROR log for UnsafePathError, got {len(matches)}"
    assert matches[0]["exception"] is None


@pytest.mark.asyncio
async def test_unsafe_path_error_response_matches_generic_configuration_error(
    client: AsyncClient,
) -> None:
    """Status code and body shape are unchanged from the generic
    ``ConfigurationError`` handler -- this task changes logging, not API
    behaviour."""
    unsafe_response = await client.get("/trigger/unsafe-path")
    generic_response = await client.get("/trigger/configuration-error")

    assert unsafe_response.status_code == generic_response.status_code == 500
    assert (
        unsafe_response.json()
        == generic_response.json()
        == {"detail": "Server configuration error"}
    )


@pytest.mark.asyncio
async def test_generic_configuration_error_still_logs_a_traceback(
    client: AsyncClient, captured_records: list[dict]
) -> None:
    """Registering a specific ``UnsafePathError`` handler must not regress the
    generic ``ConfigurationError`` handler for *other* subclasses -- proves
    Starlette's MRO-based dispatch (``type(exc).__mro__``) picks the specific
    handler for ``UnsafePathError`` instances and the generic one otherwise,
    rather than one registration shadowing the other outright."""
    response = await client.get("/trigger/configuration-error")
    assert response.status_code == 500

    matches = [r for r in captured_records if r["message"] == "Configuration error"]
    assert len(matches) == 1
    assert matches[0]["exception"] is not None
