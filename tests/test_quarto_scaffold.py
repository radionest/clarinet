"""Unit tests for clarinet.utils.quarto_scaffold."""

import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from clarinet.exceptions.domain import QuartoScaffoldError
from clarinet.utils.quarto_scaffold import (
    build_qmd_text,
    generate_default_reference,
    strip_docx_body,
)


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


# ---------------------------------------------------------------------------
# Namespace-preservation test (Word 2016+ documents with extra prefixes)
# ---------------------------------------------------------------------------

_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"

_DOCUMENT_XML_EXTRA_NS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:document xmlns:w="{_W}" xmlns:r="{_R}"'
    f' xmlns:mc="{_MC_NS}" xmlns:w14="{_W14_NS}"'
    f' mc:Ignorable="w14">'
    "<w:body>"
    "<w:p><w:r><w:t>BODY TEXT TO STRIP</w:t></w:r></w:p>"
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
    "</w:body></w:document>"
)


def _make_docx_extra_ns(path: Path) -> Path:
    """Build a .docx whose document.xml root declares extra namespace prefixes."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        z.writestr("word/document.xml", _DOCUMENT_XML_EXTRA_NS)
        z.writestr("word/styles.xml", _STYLES_XML)
    return path


def test_strip_preserves_extra_namespaces(tmp_path: Path) -> None:
    src = _make_docx_extra_ns(tmp_path / "in.docx")
    dest = tmp_path / "reference.docx"
    strip_docx_body(src, dest)
    with zipfile.ZipFile(dest) as z:
        doc = z.read("word/document.xml").decode("utf-8")

    # Body text gone, sectPr kept
    assert "BODY TEXT TO STRIP" not in doc
    assert "sectPr" in doc

    # Prefixes that appear in attributes are preserved verbatim (mc:Ignorable).
    # ET drops namespace declarations for prefixes unused after body stripping
    # (e.g. w14 is declared but no element uses it), but prefixes that are
    # actively used must not be renamed to ns0/ns1.
    assert "xmlns:mc=" in doc
    assert "mc:Ignorable=" in doc
    assert "ns0" not in doc
    assert "ns1" not in doc


# ---------------------------------------------------------------------------
# generate_default_reference tests
# ---------------------------------------------------------------------------


def test_generate_default_reference_writes_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"PKdocxbytes", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "reference.docx"
    generate_default_reference(dest, Path("/opt/quarto/bin/quarto"))

    assert dest.read_bytes() == b"PKdocxbytes"
    assert captured["cmd"][1:] == ["pandoc", "--print-default-data-file", "reference.docx"]


def test_generate_default_reference_raises_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(QuartoScaffoldError):
        generate_default_reference(tmp_path / "reference.docx", Path("/opt/quarto/bin/quarto"))


# ---------------------------------------------------------------------------
# scaffold_quarto_report tests
# ---------------------------------------------------------------------------

from clarinet.exceptions.domain import QuartoNotInstalledError  # noqa: E402
from clarinet.utils.quarto_scaffold import scaffold_quarto_report  # noqa: E402

_QUARTO_RENDER = "clarinet.utils.quarto_scaffold.resolve_quarto_executable"


def _patch_quarto(monkeypatch: pytest.MonkeyPatch, exe: Path | None) -> None:
    monkeypatch.setattr(_QUARTO_RENDER, lambda: exe)


def test_scaffold_default_creates_qmd_and_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_quarto(monkeypatch, tmp_path / "quarto")
    monkeypatch.setattr(
        "clarinet.utils.quarto_scaffold.generate_default_reference",
        lambda dest, exe: dest.write_bytes(b"PKref"),
    )
    qmd = scaffold_quarto_report(
        "summary",
        title="Сводка",
        formats=["docx"],
        data_reports=["demo_records"],
        reports_dir=tmp_path,
    )
    assert qmd == tmp_path / "summary.qmd"
    assert qmd.read_text(encoding="utf-8").count("reference.docx") >= 1
    assert (tmp_path / "reference.docx").read_bytes() == b"PKref"


def test_scaffold_from_docx_uses_strip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = _make_docx(tmp_path / "brand.docx")
    qmd = scaffold_quarto_report(
        "rep",
        formats=["docx"],
        data_reports=[],
        from_docx=src,
        reports_dir=tmp_path,
    )
    assert qmd.exists()
    with zipfile.ZipFile(tmp_path / "reference.docx") as z:
        assert "SECRET PATIENT TEXT" not in z.read("word/document.xml").decode("utf-8")


def test_scaffold_existing_qmd_without_force_raises(tmp_path: Path) -> None:
    (tmp_path / "rep.qmd").write_text("old")
    with pytest.raises(QuartoScaffoldError):
        scaffold_quarto_report("rep", formats=["docx"], data_reports=[], reports_dir=tmp_path)


def test_scaffold_default_keeps_existing_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "reference.docx").write_bytes(b"EXISTING")
    _patch_quarto(monkeypatch, None)  # would raise if generation were attempted
    scaffold_quarto_report("rep", formats=["docx"], data_reports=[], reports_dir=tmp_path)
    assert (tmp_path / "reference.docx").read_bytes() == b"EXISTING"


def test_scaffold_default_no_quarto_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_quarto(monkeypatch, None)
    with pytest.raises(QuartoNotInstalledError):
        scaffold_quarto_report("rep", formats=["docx"], data_reports=[], reports_dir=tmp_path)


def test_scaffold_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(QuartoScaffoldError):
        scaffold_quarto_report("../evil", formats=["docx"], data_reports=[], reports_dir=tmp_path)


def test_scaffold_pdf_only_skips_reference(tmp_path: Path) -> None:
    qmd = scaffold_quarto_report("rep", formats=["pdf"], data_reports=[], reports_dir=tmp_path)
    assert not (tmp_path / "reference.docx").exists()
    assert "reference-doc" not in qmd.read_text(encoding="utf-8")
