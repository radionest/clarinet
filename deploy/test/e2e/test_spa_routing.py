from conftest import assert_url_has_prefix
from playwright.sync_api import Page


def test_direct_url_access(auth_page: Page, base_url: str, path_prefix: str):
    """Direct URL access works (no 404)."""
    auth_page.goto(f"{base_url}/studies")
    auth_page.wait_for_load_state("networkidle")
    assert_url_has_prefix(auth_page, path_prefix)
    assert "404" not in auth_page.content()


def test_browser_back_forward(auth_page: Page, base_url: str, path_prefix: str):
    """Browser back/forward preserves the path prefix."""
    auth_page.goto(f"{base_url}/studies")
    auth_page.wait_for_load_state("networkidle")
    auth_page.goto(f"{base_url}/patients")
    auth_page.wait_for_load_state("networkidle")

    auth_page.go_back()
    auth_page.wait_for_load_state("networkidle")
    assert_url_has_prefix(auth_page, path_prefix)

    auth_page.go_forward()
    auth_page.wait_for_load_state("networkidle")
    assert_url_has_prefix(auth_page, path_prefix)


def test_page_refresh_preserves_route(auth_page: Page, base_url: str, path_prefix: str):
    """Page refresh doesn't break SPA routing."""
    auth_page.goto(f"{base_url}/studies")
    auth_page.wait_for_load_state("networkidle")
    auth_page.reload()
    auth_page.wait_for_load_state("networkidle")
    assert_url_has_prefix(auth_page, path_prefix)
