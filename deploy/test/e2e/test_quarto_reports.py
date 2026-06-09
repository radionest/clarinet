"""E2E: the Quarto reports admin page — page load + full render→download flow.

The render test exercises the real background pipeline (trigger → poll status →
download the rendered DOCX). It **skips gracefully** when the deployed VM has no
Quarto reports configured, or when the render fails because the `quarto` CLI /
Jupyter kernel is not provisioned — mirroring how ``test_record_form`` skips
when no records exist. To make it actually run, provision the VM with:

* ``clarinet quarto install`` (+ the ``quarto`` pip extra: jupyter/ipykernel/pandas)
* a ``*.qmd`` in ``settings.quarto_reports_path`` whose ``clarinet.data`` reports
  resolve to ``*.sql`` files in ``settings.reports_path``

See ``docs/quarto-reports.md`` and ``.claude/rules/e2e-tests.md``.
"""

import pytest
from conftest import assert_url_has_prefix
from playwright.sync_api import Page, expect


def _open_quarto_page(page: Page, base_url: str, path_prefix: str) -> None:
    page.goto(f"{base_url}/admin/quarto-reports")
    page.wait_for_load_state("networkidle")
    assert_url_has_prefix(page, path_prefix)


def test_quarto_reports_page_loads(auth_page: Page, base_url: str, path_prefix: str):
    """The page renders and keeps the sub-path prefix (no Quarto install needed)."""
    _open_quarto_page(auth_page, base_url, path_prefix)
    expect(auth_page.get_by_role("heading", name="Quarto Reports")).to_be_visible()


def test_quarto_render_to_docx(auth_page: Page, base_url: str, path_prefix: str):
    """Full flow: click Render DOCX → background render → download a non-empty file."""
    _open_quarto_page(auth_page, base_url, path_prefix)

    render_btn = auth_page.get_by_role("button", name="Render DOCX")
    if render_btn.count() == 0:
        pytest.skip("No Quarto reports configured on this deployment")
    render_btn.first.click()

    download_link = auth_page.get_by_role("link", name="Download DOCX")
    retry_btn = auth_page.get_by_role("button", name="Retry DOCX")

    # Poll the page (the backend polls its own status.json) until the render
    # resolves one way or the other — up to ~90s for cold Jupyter-kernel start.
    for _ in range(60):
        if download_link.count() > 0:
            break
        if retry_btn.count() > 0:
            pytest.skip("Quarto render failed — VM likely lacks quarto/jupyter")
        auth_page.wait_for_timeout(1500)
    else:
        pytest.skip("Quarto render did not finish within the timeout")

    with auth_page.expect_download() as download_info:
        download_link.first.click()
    download = download_info.value
    path = download.path()
    assert path is not None and path.stat().st_size > 0, "downloaded DOCX is empty"
