import pytest
from conftest import assert_url_has_prefix
from playwright.sync_api import Page


def test_formosh_submit_url_has_prefix(auth_page: Page, base_url: str, path_prefix: str):
    """Formosh web component receives submit URL with the correct path prefix."""
    auth_page.goto(f"{base_url}/records")
    auth_page.wait_for_load_state("networkidle")

    first_record = auth_page.locator("a[href*='/records/']").first
    if first_record.count() == 0:
        pytest.skip("No records available for testing")

    first_record.click()
    auth_page.wait_for_load_state("networkidle")
    assert_url_has_prefix(auth_page, path_prefix)

    formosh = auth_page.locator("formosh-form")
    if formosh.count() > 0:
        submit_url = formosh.get_attribute("submit-url") or ""
        assert submit_url.startswith(path_prefix), (
            f"Formosh submit-url '{submit_url}' missing prefix '{path_prefix}'"
        )
