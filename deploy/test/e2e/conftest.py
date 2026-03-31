"""E2E test fixtures for Playwright against deployed VM.

Required env vars:
    CLARINET_TEST_URL: e.g. https://192.168.122.10/liver_nir/
    CLARINET_TEST_ADMIN_PASSWORD: admin password
"""

import os
from urllib.parse import urlparse

import pytest
from playwright.sync_api import Page


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} not set")
    return value


@pytest.fixture(scope="session")
def base_url() -> str:
    """Full base URL including sub-path, no trailing slash."""
    return _require_env("CLARINET_TEST_URL").rstrip("/")


@pytest.fixture(scope="session")
def path_prefix(base_url: str) -> str:
    """Just the path prefix, e.g. '/liver_nir'."""
    return urlparse(base_url).path.rstrip("/")


@pytest.fixture(scope="session")
def admin_password() -> str:
    return _require_env("CLARINET_TEST_ADMIN_PASSWORD")


@pytest.fixture(scope="session")
def admin_email() -> str:
    return os.environ.get("CLARINET_TEST_ADMIN_EMAIL", "admin@clarinet.ru")


@pytest.fixture(scope="session")
def browser_context_args() -> dict:
    """Playwright pytest plugin picks this up automatically."""
    return {"ignore_https_errors": True}


@pytest.fixture
def auth_page(page: Page, base_url: str, admin_email: str, admin_password: str) -> Page:
    """Page with active admin session."""
    page.goto(f"{base_url}/login")
    page.fill('input[name="email"]', admin_email)
    page.fill('input[name="password"]', admin_password)
    page.click('button[type="submit"]')
    page.wait_for_url(f"**{urlparse(base_url).path}/**")
    return page


def assert_url_has_prefix(page: Page, path_prefix: str) -> None:
    """Assert current URL contains the expected path prefix."""
    current_path = urlparse(page.url).path
    assert current_path.startswith(path_prefix), (
        f"URL lost prefix: expected '{path_prefix}...', got '{current_path}'"
    )
