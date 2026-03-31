from conftest import assert_url_has_prefix
from playwright.sync_api import Page


def test_login_redirects_to_home(
    page: Page, base_url: str, path_prefix: str, admin_email: str, admin_password: str
):
    """Login → redirect preserves sub-path."""
    page.goto(f"{base_url}/login")
    page.fill('input[name="email"]', admin_email)
    page.fill('input[name="password"]', admin_password)
    page.click('button[type="submit"]')
    page.wait_for_url(f"**{path_prefix}/**")
    assert_url_has_prefix(page, path_prefix)


def test_unauthenticated_redirect_to_login(page: Page, base_url: str, path_prefix: str):
    """Unauthenticated access → redirect to login with prefix preserved."""
    page.goto(f"{base_url}/records")
    page.wait_for_url(f"**{path_prefix}/login**")
    assert_url_has_prefix(page, path_prefix)
