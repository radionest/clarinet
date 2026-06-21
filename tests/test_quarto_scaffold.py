"""Unit tests for clarinet.utils.quarto_scaffold."""

import yaml

from clarinet.utils.quarto_scaffold import build_qmd_text


def _front_matter(qmd: str) -> dict:
    """Parse the leading YAML block of a generated .qmd back into a dict."""
    assert qmd.startswith("---\n")
    _, fm, _body = qmd.split("---\n", 2)
    return yaml.safe_load(fm)


def test_build_qmd_docx_with_reference_and_data() -> None:
    qmd = build_qmd_text(
        title="Сводка",
        description="desc",
        lang="ru",
        formats=["docx"],
        data_reports=["demo_records"],
        reference_doc="reference.docx",
    )
    fm = _front_matter(qmd)
    assert fm["title"] == "Сводка"
    assert fm["lang"] == "ru"
    assert fm["format"]["docx"]["reference-doc"] == "reference.docx"
    assert fm["clarinet"]["data"] == ["demo_records"]
    assert qmd.rstrip().endswith("#")  # trailing empty heading


def test_build_qmd_omits_data_when_empty() -> None:
    qmd = build_qmd_text(
        title="t",
        description="",
        lang="ru",
        formats=["docx"],
        data_reports=[],
        reference_doc="reference.docx",
    )
    assert "clarinet" not in _front_matter(qmd)


def test_build_qmd_no_reference_doc_when_none() -> None:
    qmd = build_qmd_text(
        title="t",
        description="",
        lang="ru",
        formats=["docx"],
        data_reports=[],
        reference_doc=None,
    )
    assert "reference-doc" not in qmd


def test_build_qmd_both_formats() -> None:
    qmd = build_qmd_text(
        title="t",
        description="",
        lang="en",
        formats=["docx", "pdf"],
        data_reports=[],
        reference_doc="reference.docx",
    )
    fm = _front_matter(qmd)
    assert "docx" in fm["format"]
    assert "pdf" in fm["format"]
