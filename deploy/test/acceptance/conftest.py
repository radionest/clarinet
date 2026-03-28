"""Acceptance test fixtures for deployed Clarinet instance.

Required environment variables:
    CLARINET_TEST_URL: Base URL (e.g. https://192.168.122.10/nir_liver/)
    CLARINET_TEST_ADMIN_PASSWORD: Admin password from settings.toml
"""

import os

import httpx
import pytest


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} not set")
    return value


@pytest.fixture(scope="session")
def base_url() -> str:
    url = _require_env("CLARINET_TEST_URL").rstrip("/")
    # Ensure it ends with /api for API calls
    if not url.endswith("/api"):
        url = f"{url}/api"
    return url


@pytest.fixture(scope="session")
def admin_password() -> str:
    return _require_env("CLARINET_TEST_ADMIN_PASSWORD")


@pytest.fixture(scope="session")
def admin_email() -> str:
    return os.environ.get("CLARINET_TEST_ADMIN_EMAIL", "admin@clarinet.ru")


@pytest.fixture
def api_client(base_url: str) -> httpx.Client:
    """Unauthenticated HTTP client."""
    with httpx.Client(base_url=base_url, verify=False, follow_redirects=True) as client:
        yield client


@pytest.fixture
def auth_client(base_url: str, admin_email: str, admin_password: str) -> httpx.Client:
    """Authenticated HTTP client with session cookie."""
    with httpx.Client(base_url=base_url, verify=False, follow_redirects=True) as client:
        response = client.post(
            "/auth/login",
            data={"username": admin_email, "password": admin_password},
        )
        assert response.status_code in (200, 204), (
            f"Login failed: {response.status_code} {response.text}"
        )
        yield client
