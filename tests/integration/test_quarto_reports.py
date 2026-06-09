"""Integration tests for the Quarto reports endpoints."""

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from clarinet.api.app import app
from clarinet.api.dependencies import get_quarto_report_registry
from clarinet.models.quarto_report import QuartoReportTemplate
from clarinet.services.quarto_report_service import QuartoReportRegistry
from tests.conftest import create_authenticated_client, create_mock_superuser
from tests.utils.urls import (
    ADMIN_QUARTO_REPORTS,
    admin_quarto_render,
    admin_quarto_render_download,
    admin_quarto_render_status,
)

pytestmark = pytest.mark.asyncio


def _make_registry() -> QuartoReportRegistry:
    """Registry with one template that declares no data reports.

    No ``clarinet.data`` means rendering needs no SQL registry, keeping the
    error-path tests free of DB coupling. The on-disk ``.qmd`` path is never
    read by these tests (none of them reach a successful render).
    """
    return QuartoReportRegistry(
        [
            (
                QuartoReportTemplate(
                    name="demo",
                    title="Demo",
                    description="A demo report",
                    data_reports=[],
                ),
                Path("/tmp/demo.qmd"),
            )
        ]
    )


@pytest_asyncio.fixture
async def quarto_client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Authenticated superuser client with a fixed Quarto report registry."""
    mock_user = await create_mock_superuser(test_session, email="quarto@test.com")
    app.dependency_overrides[get_quarto_report_registry] = _make_registry

    async for ac in create_authenticated_client(mock_user, test_session, test_settings):
        yield ac

    app.dependency_overrides.pop(get_quarto_report_registry, None)


@pytest_asyncio.fixture
async def unauth_client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Unauthenticated client for 401 checks."""
    from clarinet.utils.database import get_async_session

    async def override_get_session() -> AsyncGenerator:
        yield test_session

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[get_quarto_report_registry] = _make_registry

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_list_quarto_reports(quarto_client: AsyncClient) -> None:
    resp = await quarto_client.get(ADMIN_QUARTO_REPORTS)
    assert resp.status_code == 200
    data = resp.json()
    assert [t["name"] for t in data] == ["demo"]
    assert data[0]["data_reports"] == []


async def test_list_requires_auth(unauth_client: AsyncClient) -> None:
    resp = await unauth_client.get(ADMIN_QUARTO_REPORTS)
    assert resp.status_code == 401


async def test_render_unknown_report_returns_404(quarto_client: AsyncClient) -> None:
    resp = await quarto_client.post(admin_quarto_render("nope"), json={"formats": ["docx"]})
    assert resp.status_code == 404


async def test_render_empty_formats_returns_422(quarto_client: AsyncClient) -> None:
    """Empty formats would render "into nothing": DONE with no files, download 409 forever."""
    resp = await quarto_client.post(admin_quarto_render("demo"), json={"formats": []})
    assert resp.status_code == 422


async def test_render_returns_503_when_quarto_missing(
    quarto_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A known template still 503s (not 500) when the quarto binary is absent."""
    from clarinet.services import quarto_render

    monkeypatch.setattr(quarto_render, "resolve_quarto_executable", lambda: None)
    resp = await quarto_client.post(admin_quarto_render("demo"), json={"formats": ["docx"]})
    assert resp.status_code == 503


async def test_status_unknown_render_returns_404(quarto_client: AsyncClient) -> None:
    resp = await quarto_client.get(admin_quarto_render_status("demo", "20260101_000000_000000"))
    assert resp.status_code == 404


async def test_download_unknown_render_returns_404(quarto_client: AsyncClient) -> None:
    resp = await quarto_client.get(admin_quarto_render_download("demo", "20260101_000000_000000"))
    assert resp.status_code == 404
