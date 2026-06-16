"""Endpoint tests for GET /api/events (SSE stream).

The stream is a plain HTTP GET, so it behaves like any other authenticated
endpoint: no cookie -> 401 before the handler runs (covered over HTTP). The
handler's response construction is tested by calling it directly — iterating
the body over httpx's ASGI transport would block on the long-lived generator,
so the generator's internal loop (ping/revalidate/queue) is verified manually
in Phase 3 and by the bus unit tests, not here.
"""

import asyncio
import contextlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from clarinet.api.routers.sse import events_stream
from clarinet.services.events import bus as bus_mod
from clarinet.utils.db_manager import db_manager
from tests.utils.factories import make_user
from tests.utils.urls import SSE_URL


def _request() -> Request:
    return Request(
        {"type": "http", "method": "GET", "path": SSE_URL, "headers": [], "query_string": b""}
    )


@pytest.mark.asyncio
async def test_sse_rejects_without_cookie(unauthenticated_client):
    resp = await unauthenticated_client.get(SSE_URL)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_sse_handshake_builds_event_stream(test_session, monkeypatch):
    @contextlib.asynccontextmanager
    async def fake_session_ctx():
        yield test_session

    # _load_allowed_types opens a short-lived session via db_manager; point it
    # at the test session (no query runs for a superuser with no roles).
    monkeypatch.setattr(db_manager, "get_async_session_context", fake_session_ctx)
    bus_mod.set_event_bus(bus_mod.EventBus(asyncio.get_running_loop()))
    try:
        resp = await events_stream(_request(), make_user(is_superuser=True))
        assert resp.status_code == 200
        assert resp.media_type == "text/event-stream"
        assert resp.headers["x-accel-buffering"] == "no"
    finally:
        bus_mod.set_event_bus(None)


@pytest.mark.asyncio
async def test_sse_unavailable_without_bus(monkeypatch):
    bus_mod.set_event_bus(None)
    with pytest.raises(HTTPException) as exc:
        await events_stream(_request(), make_user(is_superuser=True))
    assert exc.value.status_code == 503
