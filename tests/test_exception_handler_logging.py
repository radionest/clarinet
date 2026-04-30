"""Regression tests for 422 exception-handler logging.

Each 422 handler in `clarinet/api/exception_handlers.py` must emit an ERROR
record with a traceback so blocking client failures (e.g. DICOMweb metadata
422 that prevents OHIF from opening a study) stay visible in clarinet.log.
A refactor that drops the log call would silently regress observability.
"""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import ArgumentError, InvalidRequestError, StatementError

from clarinet.api.exception_handlers import setup_exception_handlers
from clarinet.exceptions.domain import SlicerError, ValidationError
from clarinet.utils.logger import logger


@pytest.fixture
def captured_records():
    records: list[dict] = []
    sink_id = logger.add(lambda msg: records.append(msg.record), level="DEBUG")
    yield records
    logger.remove(sink_id)


@pytest_asyncio.fixture
async def client():
    """Tiny FastAPI app with one trigger route per 422 handler."""
    app = FastAPI()
    setup_exception_handlers(app)

    @app.get("/trigger/validation")
    async def _validation() -> None:
        raise ValidationError("invalid payload")

    @app.get("/trigger/slicer")
    async def _slicer() -> None:
        raise SlicerError("slicer boom")

    @app.get("/trigger/overflow")
    async def _overflow() -> None:
        raise OverflowError("too big")

    @app.get("/trigger/value")
    async def _value() -> None:
        raise ValueError("bad value")

    @app.get("/trigger/argument")
    async def _argument() -> None:
        raise ArgumentError("ambiguous join")

    @app.get("/trigger/invalid_request")
    async def _invalid_request() -> None:
        raise InvalidRequestError("invalid request")

    @app.get("/trigger/statement")
    async def _statement() -> None:
        raise StatementError("bad statement", "SELECT 1", None, ValueError("orig"))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.parametrize(
    ("route", "exc_label"),
    [
        ("/trigger/validation", "ValidationError"),
        ("/trigger/slicer", "SlicerError"),
        ("/trigger/overflow", "OverflowError"),
        ("/trigger/value", "ValueError"),
        ("/trigger/argument", "ArgumentError"),
        ("/trigger/invalid_request", "InvalidRequestError"),
        ("/trigger/statement", "StatementError"),
    ],
)
@pytest.mark.asyncio
async def test_422_handler_logs_error_with_traceback(
    client: AsyncClient,
    captured_records: list[dict],
    route: str,
    exc_label: str,
) -> None:
    response = await client.get(route)
    assert response.status_code == 422

    matches = [r for r in captured_records if f"422 {exc_label}" in r["message"]]
    assert len(matches) == 1, f"expected one ERROR log for {exc_label}, got {len(matches)}"

    record = matches[0]
    assert record["level"].name == "ERROR"
    # `logger.opt(exception=exc)` attaches the exception so the JSONL sink renders `.exc`
    assert record["exception"] is not None
    # Method + path must appear in the message so operators can grep by route
    assert f"GET {route}" in record["message"]
