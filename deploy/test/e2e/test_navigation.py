from conftest import assert_url_has_prefix
from playwright.sync_api import Page


def test_sidebar_navigation_preserves_prefix(auth_page: Page, path_prefix: str):
    """Navigation link hrefs all contain the path prefix."""
    nav_links = auth_page.locator("nav a, .sidebar a").all()
    for link in nav_links:
        href = link.get_attribute("href")
        if href and href.startswith("/"):
            assert href.startswith(path_prefix), f"Nav link '{href}' missing prefix '{path_prefix}'"


def test_page_transitions_preserve_prefix(auth_page: Page, base_url: str, path_prefix: str):
    """Page transitions between routes preserve the path prefix."""
    routes = ["/studies", "/patients", "/records"]
    for route in routes:
        auth_page.goto(f"{base_url}{route}")
        auth_page.wait_for_load_state("networkidle")
        assert_url_has_prefix(auth_page, path_prefix)
