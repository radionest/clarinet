"""Regression tests: application startup with different pipeline settings.

Verifies that ``lifespan()`` completes without errors under various
configurations, particularly when ``pipeline_enabled=True``.

The original bug: ``PipelineChainMiddleware.startup()`` called
``ClarinetClient.login()`` (HTTP to own API) during lifespan, before
uvicorn accepted connections — causing "All connection attempts failed".
The fix uses lazy ``_ensure_client()`` instead.  These tests catch
a regression if the eager login returns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from src.api.app import lifespan
from src.settings import settings
from src.utils.db_manager import db_manager


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset global singletons so each test gets a fresh engine/broker."""
    import src.services.pipeline.broker as broker_mod

    orig_broker = broker_mod._broker

    db_manager._async_engine = None
    db_manager._async_session_factory = None
    broker_mod._broker = None

    yield

    db_manager._async_engine = None
    db_manager._async_session_factory = None
    broker_mod._broker = orig_broker


@pytest.fixture
def startup_settings(monkeypatch, tmp_path):
    """Patch the global ``settings`` singleton for startup tests.

    Uses a real SQLite file in ``tmp_path`` so the lifespan can create
    tables and bootstrap data without interfering with other tests.
    """
    monkeypatch.setattr(settings, "database_name", str(tmp_path / "test_startup"))
    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.setattr(settings, "pipeline_enabled", False)
    monkeypatch.setattr(settings, "recordflow_enabled", False)
    monkeypatch.setattr(settings, "session_cleanup_enabled", False)
    monkeypatch.setattr(settings, "dicomweb_enabled", False)
    monkeypatch.setattr(settings, "frontend_enabled", False)
    monkeypatch.setattr(settings, "admin_password", "TestStartup123!")


# ── Test 1: pipeline disabled ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_pipeline_disabled(startup_settings, capture_logs):
    """App starts without errors when ``pipeline_enabled=False``.

    The pipeline broker must NOT be created.
    """
    app = FastAPI(lifespan=lifespan)

    async with lifespan(app):
        assert not hasattr(app.state, "pipeline_broker") or app.state.pipeline_broker is None

    errors = [m for m in capture_logs if "client" in m.lower() or "login" in m.lower()]
    assert errors == [], f"Unexpected client/login errors during startup: {errors}"


# ── Test 2: pipeline enabled + real RabbitMQ ────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.pipeline
async def test_startup_pipeline_enabled(
    startup_settings, capture_logs, _check_rabbitmq, monkeypatch
):
    """App starts with ``pipeline_enabled=True`` and a real RabbitMQ broker.

    **Main regression test**: no "client login" or "All connection attempts
    failed" errors must appear.  The pipeline broker must be created.
    """
    from tests.integration.conftest import RABBITMQ_HOST, RABBITMQ_PORT

    monkeypatch.setattr(settings, "pipeline_enabled", True)
    monkeypatch.setattr(settings, "rabbitmq_host", RABBITMQ_HOST)
    monkeypatch.setattr(settings, "rabbitmq_port", RABBITMQ_PORT)
    monkeypatch.setattr(settings, "rabbitmq_login", "clarinet_test")
    monkeypatch.setattr(settings, "rabbitmq_password", "clarinet_test")

    app = FastAPI(lifespan=lifespan)

    async with lifespan(app):
        assert app.state.pipeline_broker is not None

    # Regression: no client/login errors during startup
    login_errors = [
        m
        for m in capture_logs
        if "client" in m.lower()
        or "login" in m.lower()
        or "connection attempts failed" in m.lower()
    ]
    assert login_errors == [], f"Regression: client login attempted during startup: {login_errors}"


# ── Test 3: pipeline enabled + RabbitMQ unavailable ─────────────────────────


@pytest.mark.asyncio
async def test_startup_pipeline_rabbitmq_unavailable(startup_settings, capture_logs, monkeypatch):
    """App survives when ``pipeline_enabled=True`` but RabbitMQ is unreachable.

    Lifespan must NOT crash.  Logs should contain a broker-related error,
    but NOT a "client" / "login" error (that would indicate the old bug).
    """
    monkeypatch.setattr(settings, "pipeline_enabled", True)

    mock_connect = AsyncMock(side_effect=ConnectionError("mocked: RabbitMQ unreachable"))

    with patch("aio_pika.connect_robust", mock_connect):
        app = FastAPI(lifespan=lifespan)

        async with lifespan(app):
            assert not hasattr(app.state, "pipeline_broker") or app.state.pipeline_broker is None

    # Should log a broker startup failure
    broker_errors = [m for m in capture_logs if "pipeline broker" in m.lower()]
    assert broker_errors, "Expected a 'Failed to start pipeline broker' log message"

    # Must NOT contain client/login errors (that's the old bug)
    login_errors = [m for m in capture_logs if "client" in m.lower() or "login" in m.lower()]
    assert login_errors == [], (
        f"Regression: client/login error appeared instead of broker error: {login_errors}"
    )
