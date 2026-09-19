"""Regression tests for three unauthenticated attack surfaces.

- ``/ohif/*`` static serving escaped its directory via ``..`` segments
- CORS reflected any origin with credentials allowed
- ``POST /api/pipelines/sync`` wrote to the DB without authentication
"""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from clarinet.api.app import create_app
from clarinet.settings import Settings, settings
from tests.utils.urls import HEALTH, PIPELINES_SYNC

EVIL_ORIGIN = "https://evil.example"
UI_ORIGIN = "https://ui.example"


@pytest.fixture
def ohif_client(tmp_path, monkeypatch) -> TestClient:
    """App serving OHIF from ``tmp_path/storage/ohif`` with a secret one level up."""
    ohif = tmp_path / "storage" / "ohif"
    ohif.mkdir(parents=True)
    (ohif / "index.html").write_text("ohif index")
    (ohif / "asset.js").write_text("ohif asset")
    (tmp_path / "storage" / "secret.txt").write_text("PATIENT-SECRET")
    monkeypatch.setattr(type(settings), "ohif_path", property(lambda _self: ohif))
    monkeypatch.setattr(settings, "ohif_enabled", True)
    return TestClient(create_app(root_path=""))


def test_ohif_serves_files_inside_its_directory(ohif_client):
    assert ohif_client.get("/ohif/asset.js").text == "ohif asset"


# httpx normalizes a literal "..", so the dots are percent-encoded; the ASGI
# server decodes them before routing, exactly like a raw client request.
@pytest.mark.parametrize("path", ["/ohif/%2e%2e/secret.txt", "/ohif/..%2fsecret.txt"])
def test_ohif_does_not_serve_files_outside_its_directory(ohif_client, path):
    response = ohif_client.get(path)

    assert "PATIENT-SECRET" not in response.text


def test_ohif_does_not_serve_an_absolute_path_remainder(ohif_client, tmp_path):
    # "/ohif//abs/path": pathlib discards the base when joining an absolute path.
    secret = tmp_path / "storage" / "secret.txt"

    response = ohif_client.get(f"/ohif/{secret}")

    assert "PATIENT-SECRET" not in response.text


def test_cors_does_not_reflect_arbitrary_origins():
    client = TestClient(create_app(root_path=""))

    response = client.get(HEALTH, headers={"Origin": EVIL_ORIGIN, "Cookie": "clarinet_session=x"})

    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin_with_credentials(monkeypatch):
    monkeypatch.setattr(settings, "cors_origins", [UI_ORIGIN])
    client = TestClient(create_app(root_path=""))

    allowed = client.get(HEALTH, headers={"Origin": UI_ORIGIN})
    denied = client.get(HEALTH, headers={"Origin": EVIL_ORIGIN})

    assert allowed.headers["access-control-allow-origin"] == UI_ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in denied.headers


def test_cors_wildcard_origin_is_rejected(monkeypatch):
    # "*" plus credentials makes Starlette echo any Origin back - the original hole.
    # Via env: Settings ignores constructor kwargs (settings_customise_sources).
    monkeypatch.setenv("CLARINET_CORS_ORIGINS", '["*"]')

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.asyncio
async def test_pipeline_sync_requires_authentication(unauthenticated_client):
    response = await unauthenticated_client.post(PIPELINES_SYNC)

    assert response.status_code == 401
