"""How internal clients (RecordFlow, pipeline tasks) reach this API."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from clarinet.settings import settings


def _url(**overrides: object) -> str:
    base = {"api_base_url": "", "host": "0.0.0.0", "port": 8111, "root_url": ""}
    return settings.model_copy(update=base | overrides).effective_api_base_url


def test_explicit_api_base_url_wins() -> None:
    url = "https://clarinet.example.org/my_project/api"
    assert _url(api_base_url=url, root_url="/other") == url


@pytest.mark.parametrize(
    ("host", "expected"),
    [("0.0.0.0", "127.0.0.1"), ("::", "[::1]"), ("10.0.0.5", "10.0.0.5"), ("::1", "[::1]")],
)
def test_derived_host(host: str, expected: str) -> None:
    assert _url(host=host) == f"http://{expected}:8111/api"


@pytest.mark.parametrize(
    ("root_url", "expected"),
    [
        ("/my_project", "/my_project"),
        ("/my_project/", "/my_project"),
        ("/", ""),
        ("", ""),
        ("my_project", "/my_project"),
    ],
)
def test_derived_url_includes_root_url(root_url: str, expected: str) -> None:
    assert _url(root_url=root_url) == f"http://127.0.0.1:8111{expected}/api"


@pytest.mark.parametrize("path", ["/my_project/api/health", "/api/health"])
def test_app_answers_with_and_without_root_prefix(path: str, monkeypatch) -> None:
    from clarinet.api.app import create_app
    from clarinet.api.routers import health

    monkeypatch.setattr(health, "_check_database", AsyncMock(return_value="ok"))
    client = TestClient(create_app(root_path="/my_project"))  # no `with`: no lifespan
    assert client.get(path).status_code == 200
