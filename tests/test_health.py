"""Unit tests for the health endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_endpoint_ok(client: AsyncClient):
    """Health endpoint returns ok when all services are up."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("ok", "degraded")
    assert data["database"] == "ok"
    assert data["pipeline"] in ("ok", "disabled")
    assert "version" in data


@pytest.mark.asyncio
async def test_health_endpoint_database_fields(client: AsyncClient):
    """Health response contains expected fields."""
    response = await client.get("/api/health")
    data = response.json()
    assert set(data.keys()) == {"status", "database", "pipeline", "version"}


@pytest.mark.asyncio
async def test_health_endpoint_no_auth_required(client: AsyncClient):
    """Health endpoint is accessible without authentication."""
    response = await client.get("/api/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_database_error():
    """Health endpoint reports database error when DB is unreachable."""
    from clarinet.api.routers.health import _check_database

    with patch("clarinet.api.routers.health.db_manager") as mock_db:
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(side_effect=ConnectionError("DB down"))
        mock_db.get_async_session_context.return_value = ctx
        result = await _check_database()
        assert result == "error"


@pytest.mark.asyncio
async def test_health_pipeline_disabled():
    """Health endpoint reports pipeline as disabled when not enabled."""
    from clarinet.api.routers.health import _check_pipeline

    with patch("clarinet.api.routers.health.settings") as mock_settings:
        mock_settings.pipeline_enabled = False
        result = _check_pipeline()
        assert result == "disabled"


@pytest.mark.asyncio
async def test_health_pipeline_ok():
    """Health endpoint reports pipeline as ok when at least one broker is registered."""
    from clarinet.api.routers.health import _check_pipeline

    with patch("clarinet.api.routers.health.settings") as mock_settings:
        mock_settings.pipeline_enabled = True
        with patch(
            "clarinet.services.pipeline.get_all_brokers",
            return_value={"clarinet.default": MagicMock()},
        ):
            result = _check_pipeline()
            assert result == "ok"


@pytest.mark.asyncio
async def test_health_pipeline_error_when_no_brokers():
    """Health endpoint reports error when pipeline_enabled but no brokers exist."""
    from clarinet.api.routers.health import _check_pipeline

    with patch("clarinet.api.routers.health.settings") as mock_settings:
        mock_settings.pipeline_enabled = True
        with patch("clarinet.services.pipeline.get_all_brokers", return_value={}):
            result = _check_pipeline()
            assert result == "error"
