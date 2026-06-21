"""Scaffolding for new Quarto reports (``clarinet quarto new``).

Symmetric to :mod:`clarinet.utils.quarto_discovery` (that module reads ``.qmd``
front matter; this one writes a fresh ``.qmd`` plus its sibling
``reference.docx`` style asset). Pure file/CLI logic — no DB, no app state.
"""

import io
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml

from clarinet.exceptions.domain import QuartoNotInstalledError, QuartoScaffoldError
from clarinet.services.quarto_render import resolve_quarto_executable
from clarinet.settings import settings
from clarinet.utils.logger import logger

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOCUMENT_PART = "word/document.xml"

# Register well-known prefixes at module level so they are always available.
ET.register_namespace("w", _W_NS)
ET.register_namespace("r", _R_NS)


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
    for _event, ns in ET.iterparse(io.BytesIO(document_xml), events=["start-ns"]):
        ET.register_namespace(str(ns[0]), str(ns[1]))
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


def generate_default_reference(dest: Path, quarto_executable: Path) -> None:
    """Write the bundled pandoc default ``reference.docx`` to ``dest``.

    ``quarto pandoc`` proxies Quarto's bundled pandoc, so no separate pandoc
    install is needed. ``--print-default-data-file reference.docx`` emits the
    docx bytes on stdout.

    Raises:
        QuartoScaffoldError: the subprocess exits non-zero.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(quarto_executable), "pandoc", "--print-default-data-file", "reference.docx"],
        capture_output=True,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()[:500]
        raise QuartoScaffoldError(f"failed to generate default reference.docx: {detail}")
    dest.write_bytes(proc.stdout)


def scaffold_quarto_report(
    name: str,
    *,
    title: str | None = None,
    description: str = "",
    lang: str = "ru",
    formats: list[str],
    data_reports: list[str],
    from_docx: Path | None = None,
    force: bool = False,
    reports_dir: Path | None = None,
) -> Path:
    """Create ``<name>.qmd`` (+ sibling ``reference.docx``) in the reports folder.

    Returns the path to the created ``.qmd``. See the plan's behavior contract
    for the full reference.docx / force / pdf-only rules.

    Raises:
        QuartoScaffoldError: invalid name, or a target exists without ``force``.
        QuartoNotInstalledError: a default reference.docx is needed but Quarto
            is not installed (``--from-docx`` does not require Quarto).
    """
    if not name or "/" in name or "\\" in name or ".." in name:
        raise QuartoScaffoldError(f"invalid report name: {name!r}")

    folder = reports_dir if reports_dir is not None else settings.get_quarto_reports_path()
    folder.mkdir(parents=True, exist_ok=True)

    qmd_path = folder / f"{name}.qmd"
    if qmd_path.exists() and not force:
        raise QuartoScaffoldError(f"{qmd_path} already exists (use --force to overwrite)")

    reference_doc_name = _prepare_reference(folder, formats, from_docx, force)

    title = title or name
    text = build_qmd_text(
        title=title,
        description=description,
        lang=lang,
        formats=formats,
        data_reports=data_reports,
        reference_doc=reference_doc_name,
    )
    qmd_path.write_text(text, encoding="utf-8")
    logger.info(f"Created Quarto report scaffold: {qmd_path}")
    return qmd_path


def _prepare_reference(
    folder: Path, formats: list[str], from_docx: Path | None, force: bool
) -> str | None:
    """Materialize ``folder/reference.docx`` per the docx/from-docx/force rules.

    Returns ``"reference.docx"`` when the .qmd should reference it, else ``None``.
    """
    if "docx" not in formats:
        if from_docx is not None:
            logger.warning("--from-docx is ignored: reference.docx applies to docx output only")
        return None

    ref_path = folder / "reference.docx"
    if from_docx is not None:
        if not from_docx.is_file() or from_docx.suffix.lower() != ".docx":
            raise QuartoScaffoldError(f"--from-docx is not a .docx file: {from_docx}")
        if ref_path.exists() and not force:
            raise QuartoScaffoldError(
                f"{ref_path} already exists (use --force to replace the shared style)"
            )
        strip_docx_body(from_docx, ref_path)
        return "reference.docx"

    if ref_path.exists():
        logger.info(f"Using existing {ref_path}")
        return "reference.docx"

    executable = resolve_quarto_executable()
    if executable is None:
        raise QuartoNotInstalledError(
            "default reference.docx needs Quarto; run 'clarinet quarto install' or pass --from-docx"
        )
    generate_default_reference(ref_path, executable)
    return "reference.docx"
