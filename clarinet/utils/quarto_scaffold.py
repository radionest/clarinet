"""Scaffolding for new Quarto reports (``clarinet quarto new``).

Symmetric to :mod:`clarinet.utils.quarto_discovery` (that module reads ``.qmd``
front matter; this one writes a fresh ``.qmd`` plus its sibling
``reference.docx`` style asset). Pure file/CLI logic — no DB, no app state.
"""

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from clarinet.exceptions.domain import QuartoScaffoldError

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOCUMENT_PART = "word/document.xml"


def build_qmd_text(
    *,
    title: str,
    description: str,
    lang: str,
    formats: list[str],
    data_reports: list[str],
    reference_doc: str | None,
) -> str:
    """Render the full ``.qmd`` text: YAML front matter + one empty heading.

    ``reference_doc`` is emitted under ``format.docx.reference-doc`` only when
    given (and only when ``docx`` is in ``formats``). ``clarinet.data`` is
    omitted entirely when ``data_reports`` is empty. Serialized with
    ``allow_unicode`` so Cyrillic titles survive verbatim.
    """
    front_matter: dict[str, object] = {
        "title": title,
        "description": description,
        "lang": lang,
    }
    fmt_block: dict[str, object] = {}
    if "docx" in formats:
        docx_opts: dict[str, object] = {}
        if reference_doc:
            docx_opts["reference-doc"] = reference_doc
        fmt_block["docx"] = docx_opts
    if "pdf" in formats:
        fmt_block["pdf"] = {}
    if fmt_block:
        front_matter["format"] = fmt_block
    if data_reports:
        front_matter["clarinet"] = {"data": list(data_reports)}

    yaml_text = yaml.safe_dump(
        front_matter, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return f"---\n{yaml_text}---\n\n# \n"


def strip_docx_body(src: Path, dest: Path) -> None:
    """Write ``dest`` = ``src`` docx with its main body emptied.

    Keeps every styling part (styles, theme, numbering, settings, headers/
    footers, media, docProps) and the trailing ``<w:sectPr>`` (page size/
    margins + header/footer references); drops all typed body content. This is
    a deliberate PHI guard — ``review/reference.docx`` is committed and shipped
    in the deploy bundle, so the source document's text must never travel with
    it.

    Raises:
        QuartoScaffoldError: ``src`` is not a zip or has no ``word/document.xml``.
    """
    ET.register_namespace("w", _W_NS)
    ET.register_namespace("r", _R_NS)
    try:
        with zipfile.ZipFile(src) as zin:
            infos = zin.infolist()
            names = zin.namelist()
            if _DOCUMENT_PART not in names:
                raise QuartoScaffoldError(f"{src} is not a valid .docx (missing {_DOCUMENT_PART})")
            parts = {name: zin.read(name) for name in names}
    except zipfile.BadZipFile as exc:
        raise QuartoScaffoldError(f"{src} is not a valid .docx (not a zip archive)") from exc

    parts[_DOCUMENT_PART] = _empty_body(parts[_DOCUMENT_PART])

    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in infos:
            zout.writestr(info, parts[info.filename])


def _empty_body(document_xml: bytes) -> bytes:
    """Return ``document_xml`` with ``<w:body>`` reduced to its ``<w:sectPr>``."""
    root = ET.fromstring(document_xml)
    body = root.find(f"{{{_W_NS}}}body")
    if body is None:
        return document_xml
    sect_pr = body.find(f"{{{_W_NS}}}sectPr")
    for child in list(body):
        body.remove(child)
    if sect_pr is not None:
        body.append(sect_pr)
    result = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result
