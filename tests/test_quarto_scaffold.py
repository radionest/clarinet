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
# PHI-leak regression: non-body parts must be scrubbed too (footnotes,
# comments incl. author names, docProps authorship/custom props), while
# styling + headers/footers + media are preserved.
# ---------------------------------------------------------------------------

_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"

_FOOTNOTES_XML = (
    f'<w:footnotes xmlns:w="{_W}">'
    '<w:footnote w:id="1"><w:p><w:r><w:t>FOOTNOTE_PHI</w:t></w:r></w:p></w:footnote>'
    "</w:footnotes>"
)
_ENDNOTES_XML = (
    f'<w:endnotes xmlns:w="{_W}">'
    '<w:endnote w:id="1"><w:p><w:r><w:t>ENDNOTE_PHI</w:t></w:r></w:p></w:endnote>'
    "</w:endnotes>"
)
_COMMENTS_XML = (
    f'<w:comments xmlns:w="{_W}">'
    '<w:comment w:id="1" w:author="Reviewer_PHI" w:initials="RP">'
    "<w:p><w:r><w:t>COMMENT_PHI</w:t></w:r></w:p></w:comment>"
    "</w:comments>"
)
_PEOPLE_XML = (
    f'<w:people xmlns:w="{_W}">'
    '<w:person w:author="Reviewer_PHI"><w:presenceInfo w:providerId="None" '
    'w:userId="Reviewer_PHI"/></w:person></w:people>'
)
_CORE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    "<cp:coreProperties "
    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<dc:title>TITLE_PHI</dc:title>"
    "<dc:creator>Author_PHI</dc:creator>"
    "<dc:subject>SUBJECT_PHI</dc:subject>"
    "<dc:description>DESC_PHI</dc:description>"
    "<cp:keywords>KEYWORDS_PHI</cp:keywords>"
    "<cp:lastModifiedBy>Editor_PHI</cp:lastModifiedBy>"
    "</cp:coreProperties>"
)
_CUSTOM_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    "<Properties "
    'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    '<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2" name="PatientName">'
    "<vt:lpwstr>CUSTOM_PHI</vt:lpwstr></property></Properties>"
)
_CONTENT_TYPES_FULL = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    f'<Types xmlns="{_CT}">'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Default Extension="rels" '
    'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/footnotes.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
    '<Override PartName="/word/endnotes.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"/>'
    '<Override PartName="/word/comments.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>'
    '<Override PartName="/docProps/core.xml" '
    'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/custom.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
    "</Types>"
)
_DOC_RELS_FULL = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    f'<Relationships xmlns="{_PKG_REL}">'
    f'<Relationship Type="{_R}/styles" Id="rId1" Target="styles.xml"/>'
    f'<Relationship Type="{_R}/header" Id="rId2" Target="header1.xml"/>'
    f'<Relationship Type="{_R}/footnotes" Id="rId7" Target="footnotes.xml"/>'
    f'<Relationship Type="{_R}/endnotes" Id="rId8" Target="endnotes.xml"/>'
    f'<Relationship Type="{_R}/comments" Id="rId9" Target="comments.xml"/>'
    "</Relationships>"
)
_ROOT_RELS_FULL = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    f'<Relationships xmlns="{_PKG_REL}">'
    f'<Relationship Id="rIdD1" Type="{_R}/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rIdD3" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
    'Target="docProps/core.xml"/>'
    f'<Relationship Id="rIdD5" Type="{_R}/custom-properties" Target="docProps/custom.xml"/>'
    "</Relationships>"
)

_PHI_TOKENS = [
    "BODY_PHI",
    "FOOTNOTE_PHI",
    "ENDNOTE_PHI",
    "COMMENT_PHI",
    "Reviewer_PHI",
    "Author_PHI",
    "Editor_PHI",
    "TITLE_PHI",
    "SUBJECT_PHI",
    "DESC_PHI",
    "KEYWORDS_PHI",
    "CUSTOM_PHI",
]


def _make_docx_with_phi(path: Path) -> Path:
    """Build a docx that carries PHI in body, footnotes/endnotes, comments
    (text + author/initials), people, and docProps (core + custom), plus a
    valid [Content_Types].xml and document rels referencing those parts.
    Styling (Heading1) and header letterhead must survive.
    """
    body_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" xmlns:r="{_R}">'
        "<w:body>"
        "<w:p><w:r><w:t>BODY_PHI</w:t></w:r></w:p>"
        '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/></w:sectPr>'
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES_FULL)
        z.writestr("_rels/.rels", _ROOT_RELS_FULL)
        z.writestr("word/document.xml", body_xml)
        z.writestr("word/_rels/document.xml.rels", _DOC_RELS_FULL)
        z.writestr("word/styles.xml", _STYLES_XML)
        z.writestr("word/header1.xml", _HEADER_XML.replace("ORG LETTERHEAD", "ORG_LETTERHEAD"))
        z.writestr("word/footnotes.xml", _FOOTNOTES_XML)
        z.writestr("word/endnotes.xml", _ENDNOTES_XML)
        z.writestr("word/comments.xml", _COMMENTS_XML)
        z.writestr("word/people.xml", _PEOPLE_XML)
        z.writestr("word/media/image1.png", b"\x89PNG\r\n\x1a\nLOGO_BYTES")
        z.writestr("docProps/core.xml", _CORE_XML)
        z.writestr("docProps/custom.xml", _CUSTOM_XML)
    return path


def test_strip_scrubs_all_phi_bearing_parts(tmp_path: Path) -> None:
    src = _make_docx_with_phi(tmp_path / "phi.docx")
    dest = tmp_path / "reference.docx"
    strip_docx_body(src, dest)

    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        all_bytes = b"".join(z.read(n) for n in names)

    # 1) No PHI token survives in ANY zip part.
    blob = all_bytes.decode("latin-1")
    for token in _PHI_TOKENS:
        assert token not in blob, f"PHI leaked: {token}"

    # 2) Styling + header letterhead + logo media survive.
    with zipfile.ZipFile(dest) as z:
        assert "Heading1" in z.read("word/styles.xml").decode("utf-8")
        assert "ORG_LETTERHEAD" in z.read("word/header1.xml").decode("utf-8")
        assert b"LOGO_BYTES" in z.read("word/media/image1.png")
        # 3) document.xml present and structurally a docx.
        assert "word/document.xml" in z.namelist()
        ct = z.read("[Content_Types].xml").decode("utf-8")
        rels = z.read("word/_rels/document.xml.rels").decode("utf-8")

    # 4) No dangling references to dropped parts.
    for dropped in ("footnotes.xml", "endnotes.xml", "comments.xml"):
        assert f'Target="{dropped}"' not in rels, f"dangling rel to {dropped}"
        assert f"/word/{dropped}" not in ct, f"dangling Override for {dropped}"
    assert "/docProps/custom.xml" not in ct
    # core.xml is kept (scrubbed in place), so its Override stays.
    assert "/docProps/core.xml" in ct


def test_strip_drops_own_rels_of_dropped_parts(tmp_path: Path) -> None:
    """A dropped part's own _rels file (word/_rels/footnotes.xml.rels) must go."""
    src = tmp_path / "phi.docx"
    _make_docx_with_phi(src)
    # add a sidecar rels for footnotes (pandoc ships one)
    with zipfile.ZipFile(src, "a") as z:
        z.writestr(
            "word/_rels/footnotes.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{_PKG_REL}">'
            f'<Relationship Type="{_R}/hyperlink" Id="rId30" '
            'Target="http://example.com" TargetMode="External"/></Relationships>',
        )
    dest = tmp_path / "reference.docx"
    strip_docx_body(src, dest)
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
    assert "word/footnotes.xml" not in names
    assert "word/_rels/footnotes.xml.rels" not in names


def test_strip_scrubs_core_props_in_place(tmp_path: Path) -> None:
    """docProps/core.xml is kept but authorship/metadata values are blanked."""
    src = _make_docx_with_phi(tmp_path / "phi.docx")
    dest = tmp_path / "reference.docx"
    strip_docx_body(src, dest)
    with zipfile.ZipFile(dest) as z:
        assert "docProps/core.xml" in z.namelist()
        core = z.read("docProps/core.xml").decode("utf-8")
    # part still present and parseable, but the PHI values are gone
    assert "Author_PHI" not in core
    assert "Editor_PHI" not in core


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


def test_generate_default_reference_raises_on_empty_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Zero exit but empty stdout must error instead of writing a 0-byte file."""

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    dest = tmp_path / "reference.docx"
    with pytest.raises(QuartoScaffoldError):
        generate_default_reference(dest, Path("/opt/quarto/bin/quarto"))
    assert not dest.exists()


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


@pytest.mark.parametrize(
    "bad_name",
    [
        "",
        "/evil",
        "evil\\path",
        "../evil",
        ".",  # leading dot → "..qmd"
        "report.qmd",  # would double the suffix → "report.qmd.qmd"
        "C:report",  # colon (drive-relative path) not in allowlist
        "rep ort",  # space not in allowlist
        ".hidden",  # leading dot rejected
    ],
)
def test_scaffold_rejects_path_traversal(tmp_path: Path, bad_name: str) -> None:
    with pytest.raises(QuartoScaffoldError):
        scaffold_quarto_report(bad_name, formats=["docx"], data_reports=[], reports_dir=tmp_path)


def test_scaffold_accepts_valid_name(tmp_path: Path) -> None:
    """A name within the allowlist (letters/digits/._-) is accepted (pdf-only,
    so no Quarto needed)."""
    qmd = scaffold_quarto_report(
        "my-report.v2", formats=["pdf"], data_reports=[], reports_dir=tmp_path
    )
    assert qmd == tmp_path / "my-report.v2.qmd"


def test_scaffold_pdf_only_skips_reference(tmp_path: Path) -> None:
    qmd = scaffold_quarto_report("rep", formats=["pdf"], data_reports=[], reports_dir=tmp_path)
    assert not (tmp_path / "reference.docx").exists()
    assert "reference-doc" not in qmd.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# --from-docx validation sub-rules
# ---------------------------------------------------------------------------


def test_from_docx_nonexistent_raises(tmp_path: Path) -> None:
    with pytest.raises(QuartoScaffoldError):
        scaffold_quarto_report(
            "rep",
            formats=["docx"],
            data_reports=[],
            from_docx=tmp_path / "nope.docx",
            reports_dir=tmp_path,
        )


def test_from_docx_wrong_suffix_raises(tmp_path: Path) -> None:
    bad = tmp_path / "brand.txt"
    bad.write_bytes(b"not a docx")
    with pytest.raises(QuartoScaffoldError):
        scaffold_quarto_report(
            "rep",
            formats=["docx"],
            data_reports=[],
            from_docx=bad,
            reports_dir=tmp_path,
        )


def test_from_docx_existing_reference_without_force_raises(tmp_path: Path) -> None:
    (tmp_path / "reference.docx").write_bytes(b"EXISTING")
    src = _make_docx(tmp_path / "brand.docx")
    with pytest.raises(QuartoScaffoldError):
        scaffold_quarto_report(
            "rep",
            formats=["docx"],
            data_reports=[],
            from_docx=src,
            force=False,
            reports_dir=tmp_path,
        )


def test_from_docx_existing_reference_with_force_overwrites(tmp_path: Path) -> None:
    (tmp_path / "reference.docx").write_bytes(b"OLD")
    src = _make_docx(tmp_path / "brand.docx")
    qmd = scaffold_quarto_report(
        "rep",
        formats=["docx"],
        data_reports=[],
        from_docx=src,
        force=True,
        reports_dir=tmp_path,
    )
    assert qmd.exists()
    # reference.docx was replaced (body stripped, no longer the sentinel bytes)
    ref_bytes = (tmp_path / "reference.docx").read_bytes()
    assert ref_bytes != b"OLD"


# ---------------------------------------------------------------------------
# title or name fallback
# ---------------------------------------------------------------------------


def test_scaffold_title_defaults_to_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_quarto(monkeypatch, tmp_path / "quarto")
    monkeypatch.setattr(
        "clarinet.utils.quarto_scaffold.generate_default_reference",
        lambda dest, exe: dest.write_bytes(b"PKref"),
    )
    qmd = scaffold_quarto_report(
        "my_report",
        formats=["docx"],
        data_reports=[],
        reports_dir=tmp_path,
    )
    fm = _front_matter(qmd.read_text(encoding="utf-8"))
    assert fm["title"] == "my_report"


# ---------------------------------------------------------------------------
# CLI wiring tests
# ---------------------------------------------------------------------------

import argparse  # noqa: E402

from clarinet.cli.main import cmd_quarto_new  # noqa: E402


def test_cmd_quarto_new_maps_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_scaffold(name: str, **kwargs: object) -> Path:
        seen["name"] = name
        seen.update(kwargs)
        return tmp_path / f"{name}.qmd"

    monkeypatch.setattr("clarinet.cli.main.scaffold_quarto_report", fake_scaffold)
    args = argparse.Namespace(
        name="rep",
        title="T",
        description="",
        lang="ru",
        format="both",
        data="a, b",
        from_docx=None,
        force=False,
    )
    cmd_quarto_new(args)
    assert seen["name"] == "rep"
    assert seen["formats"] == ["docx", "pdf"]
    assert seen["data_reports"] == ["a", "b"]
    assert seen["from_docx"] is None


def test_cmd_quarto_new_exits_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(name: str, **kwargs: object) -> Path:
        raise QuartoScaffoldError("nope")

    monkeypatch.setattr("clarinet.cli.main.scaffold_quarto_report", boom)
    args = argparse.Namespace(
        name="rep",
        title=None,
        description="",
        lang="ru",
        format="docx",
        data="",
        from_docx=None,
        force=False,
    )
    with pytest.raises(SystemExit):
        cmd_quarto_new(args)


# ---------------------------------------------------------------------------
# Optional: real Quarto round-trip — a stripped reference.docx still renders.
# Skipped when Quarto is not installed.
# ---------------------------------------------------------------------------

import shutil  # noqa: E402
import sys  # noqa: E402

_QUARTO_BIN = shutil.which("quarto")


@pytest.mark.skipif(_QUARTO_BIN is None, reason="quarto CLI not installed")
@pytest.mark.skipif(sys.platform == "win32", reason="render kernel is Linux-only")
def test_stripped_reference_renders_with_quarto(tmp_path: Path) -> None:
    """The pandoc default reference.docx — which ships footnotes/comments —
    must still produce a valid docx after strip_docx_body drops those parts."""
    assert _QUARTO_BIN is not None
    src = tmp_path / "default-reference.docx"
    generate_default_reference(src, Path(_QUARTO_BIN))
    ref = tmp_path / "reference.docx"
    strip_docx_body(src, ref)

    qmd = tmp_path / "doc.qmd"
    qmd.write_text(
        "---\n"
        'title: "RT"\n'
        "format:\n"
        "  docx:\n"
        "    reference-doc: reference.docx\n"
        "---\n\n"
        "# Heading\n\nbody text\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_QUARTO_BIN, "render", str(qmd), "--to", "docx"],
        cwd=tmp_path,
        capture_output=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    assert (tmp_path / "doc.docx").is_file()
