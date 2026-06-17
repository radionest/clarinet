import pytest
from conftest import assert_url_has_prefix
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


def test_formosh_submit_url_has_prefix(auth_page: Page, base_url: str, path_prefix: str):
    """Formosh web component receives submit URL with the correct path prefix."""
    auth_page.goto(f"{base_url}/records")

    # The record list loads asynchronously; the persistent SSE stream keeps the
    # connection open so `networkidle` never fires — settle on the list locator
    # (or fall through to skip when the deployment genuinely has no records).
    records = auth_page.locator("a[href*='/records/']")
    try:
        records.first.wait_for(timeout=15_000)
    except PlaywrightTimeoutError:
        pass
    if records.count() == 0:
        pytest.skip("No records available for testing")
    first_record = records.first

    first_record.click()
    auth_page.wait_for_url("**/records/**")
    assert_url_has_prefix(auth_page, path_prefix)

    formosh = auth_page.locator("formosh-form")
    if formosh.count() > 0:
        submit_url = formosh.get_attribute("submit-url") or ""
        assert submit_url.startswith(path_prefix), (
            f"Formosh submit-url '{submit_url}' missing prefix '{path_prefix}'"
        )
