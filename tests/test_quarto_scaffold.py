"""Unit tests for clarinet.utils.quarto_scaffold."""

import zipfile
from pathlib import Path

import pytest
import yaml

from clarinet.exceptions.domain import QuartoScaffoldError
from clarinet.utils.quarto_scaffold import build_qmd_text, strip_docx_body


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


# ---------------------------------------------------------------------------
# strip_docx_body tests
# ---------------------------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

_DOCUMENT_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:document xmlns:w="{_W}" xmlns:r="{_R}">'
    "<w:body>"
    "<w:p><w:r><w:t>SECRET PATIENT TEXT</w:t></w:r></w:p>"
    '<w:sectPr><w:headerReference w:type="default" r:id="rId1"/>'
    '<w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
    "</w:body></w:document>"
)
_STYLES_XML = (
    f'<w:styles xmlns:w="{_W}">'
    '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
    "</w:styles>"
)
_HEADER_XML = f'<w:hdr xmlns:w="{_W}"><w:p><w:r><w:t>ORG LETTERHEAD</w:t></w:r></w:p></w:hdr>'


def _make_docx(path: Path) -> Path:
    """Build a minimal valid .docx: body text + style + header letterhead."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        z.writestr("word/document.xml", _DOCUMENT_XML)
        z.writestr("word/styles.xml", _STYLES_XML)
        z.writestr("word/header1.xml", _HEADER_XML)
    return path


def test_strip_removes_body_text(tmp_path: Path) -> None:
    src = _make_docx(tmp_path / "in.docx")
    dest = tmp_path / "reference.docx"
    strip_docx_body(src, dest)
    with zipfile.ZipFile(dest) as z:
        doc = z.read("word/document.xml").decode("utf-8")
    assert "SECRET PATIENT TEXT" not in doc
    assert "sectPr" in doc  # page setup kept
    assert "headerReference" in doc  # header link kept


def test_strip_keeps_styles_and_header(tmp_path: Path) -> None:
    src = _make_docx(tmp_path / "in.docx")
    dest = tmp_path / "reference.docx"
    strip_docx_body(src, dest)
    with zipfile.ZipFile(dest) as z:
        assert "Heading1" in z.read("word/styles.xml").decode("utf-8")
        assert "ORG LETTERHEAD" in z.read("word/header1.xml").decode("utf-8")


def test_strip_rejects_non_zip(tmp_path: Path) -> None:
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(QuartoScaffoldError):
        strip_docx_body(bad, tmp_path / "out.docx")


def test_strip_rejects_missing_document_part(tmp_path: Path) -> None:
    src = tmp_path / "nodoc.docx"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("word/styles.xml", _STYLES_XML)
    with pytest.raises(QuartoScaffoldError):
        strip_docx_body(src, tmp_path / "out.docx")
