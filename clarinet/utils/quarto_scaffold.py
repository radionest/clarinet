"""Scaffolding for new Quarto reports (``clarinet quarto new``).

Symmetric to :mod:`clarinet.utils.quarto_discovery` (that module reads ``.qmd``
front matter; this one writes a fresh ``.qmd`` plus its sibling
``reference.docx`` style asset). Pure file/CLI logic — no DB, no app state.
"""

import io
import os
import re
import subprocess
import tempfile
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
_CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOCUMENT_PART = "word/document.xml"
_CONTENT_TYPES_PART = "[Content_Types].xml"
_DOCUMENT_RELS_PART = "word/_rels/document.xml.rels"
_SETTINGS_PART = "word/settings.xml"
# docProps/core.xml is kept but its authorship/metadata text is blanked in place.
_CORE_PROPS_PART = "docProps/core.xml"

# strip_docx_body uses an ALLOWLIST: only known styling / layout / letterhead
# parts are kept; everything else is dropped. A denylist proved fragile —
# reviewers kept finding new PHI-bearing parts (embeddings, charts, glossary,
# customXml, …) — so the safe default is "drop unless explicitly recognised".
#
# Exact content-part names that are kept (after their own transforms, where
# applicable). ``_rels`` sidecars are handled generically by _is_kept: a sidecar
# is kept iff the content part it describes is kept (so settings.xml.rels,
# numbering.xml.rels, header1.xml.rels … follow their part automatically), with
# the package-root ``_rels/.rels`` always kept.
_KEEP_EXACT = frozenset(
    {
        _CONTENT_TYPES_PART,
        _DOCUMENT_PART,
        "word/styles.xml",
        "word/stylesWithEffects.xml",
        "word/numbering.xml",
        _SETTINGS_PART,
        "word/webSettings.xml",
        "word/fontTable.xml",
        _CORE_PROPS_PART,
    }
)

# Content-part prefixes kept WITH content: theme, embedded media/fonts, and the
# letterhead (headers/footers). The user explicitly chose the whole letterhead
# (logo + org header); a footer may carry a page-number field but no patient text.
_KEEP_PREFIXES = (
    "word/theme/",
    "word/media/",
    "word/fonts/",
    "word/header",
    "word/footer",
)
_ROOT_RELS_PART = "_rels/.rels"

# Report name → ``<name>.qmd`` filename. Positive allowlist (letters, digits,
# dot, underscore, hyphen) keeps the stem to a single path segment; a leading
# dot ("." → "..qmd", ".hidden") and a redundant .qmd suffix are rejected
# separately below.
_VALID_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

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


def _described_part(rels_name: str) -> str | None:
    """Map a sidecar ``_rels`` path to the content part it describes, else None.

    ``word/_rels/document.xml.rels`` → ``word/document.xml``;
    ``word/_rels/header1.xml.rels`` → ``word/header1.xml``;
    ``_rels/.rels`` (package root) → ``None`` (no single content part).
    """
    head, sep, tail = rels_name.rpartition("/_rels/")
    if not sep or not tail.endswith(".rels"):
        return None
    base = tail[: -len(".rels")]
    if not base:  # ``_rels/.rels`` → empty base
        return None
    return f"{head}/{base}" if head else base


def _is_kept(part_name: str) -> bool:
    """Allowlist predicate: is ``part_name`` a known styling/layout/letterhead part?

    A ``_rels`` sidecar is kept iff the content part it describes is kept (the
    package-root ``_rels/.rels`` is always kept). Anything else not matching
    :data:`_KEEP_EXACT` or a :data:`_KEEP_PREFIXES` prefix is dropped — the safe
    default for a PHI guard.
    """
    if part_name == _ROOT_RELS_PART:
        return True
    described = _described_part(part_name)
    if described is not None:
        return _is_kept(described)
    return part_name in _KEEP_EXACT or part_name.startswith(_KEEP_PREFIXES)


def strip_docx_body(src: Path, dest: Path) -> None:
    """Write ``dest`` = ``src`` docx reduced to its styling/layout/letterhead.

    A real ``.docx`` carries text and names in many parts, not just the main
    body, so this is a thorough PHI guard — ``review/reference.docx`` is
    committed and shipped in the deploy bundle, and the source document's text
    must never travel with it. The scrub is therefore an **allowlist**: only
    parts known to be style/layout/letterhead are kept; *everything else is
    dropped*. (A denylist proved fragile — new PHI-bearing parts kept surfacing.)

    **Kept** (the visual style template + letterhead):

    * ``word/styles.xml``, ``stylesWithEffects.xml``, ``numbering.xml``,
      ``webSettings.xml``, ``fontTable.xml``, ``word/theme/*``, ``word/fonts/*``;
    * the page geometry (``<w:sectPr>`` inside ``document.xml``);
    * headers/footers and ``word/media/*`` — by design, the user chose the whole
      letterhead (logo + org header) — kept WITH content.

    **Transformed** kept parts:

    * ``word/document.xml`` — body emptied to its trailing ``<w:sectPr>``;
    * ``docProps/core.xml`` — kept but authorship/metadata text blanked;
    * ``word/settings.xml`` — kept, but any ``<w:mailMerge>`` (DB connection
      string in ``<w:odso>``/``<w:connectString>``) is removed and author
      attributes (``w:author``, ``*lastModifiedBy``) are stripped tree-wide.

    **Dropped** (not on the allowlist): ``footnotes``/``endnotes``,
    ``comments*``/``people.xml``, ``word/embeddings/*`` (OLE/Excel),
    ``word/charts/*``, ``word/glossary/*``, ``customXml/*``, ``docProps/app.xml``
    and ``docProps/custom.xml``, plus any unrecognised part. When a part is
    dropped, its sidecar ``_rels``, its ``[Content_Types].xml`` ``<Override>``
    and any relationship pointing at it (in every kept ``*.rels``) are removed
    too, so the result has no dangling references and remains a valid ``.docx``.

    The result is written atomically (temp file + ``os.replace``) so a mid-write
    failure cannot leave a corrupt ``reference.docx`` behind.

    Raises:
        QuartoScaffoldError: ``src`` is not a zip, has no ``word/document.xml``,
            has a structurally malformed XML part, or a ``document.xml`` with no
            ``<w:body>`` (fail-safe — a malformed body must not pass through).
    """
    try:
        with zipfile.ZipFile(src) as zin:
            infos = zin.infolist()
            if _DOCUMENT_PART not in zin.namelist():
                raise QuartoScaffoldError(f"{src} is not a valid .docx (missing {_DOCUMENT_PART})")
            parts = {info.filename: zin.read(info.filename) for info in infos}
    except zipfile.BadZipFile as exc:
        raise QuartoScaffoldError(f"{src} is not a valid .docx (not a zip archive)") from exc

    kept_infos = [info for info in infos if _is_kept(info.filename)]
    dropped = {name for name in parts if not _is_kept(name)}

    try:
        parts[_DOCUMENT_PART] = _empty_body(parts[_DOCUMENT_PART])
        if _CORE_PROPS_PART in parts:
            parts[_CORE_PROPS_PART] = _scrub_core_props(parts[_CORE_PROPS_PART])
        if _SETTINGS_PART in parts:
            parts[_SETTINGS_PART] = _scrub_settings(parts[_SETTINGS_PART])
        if _CONTENT_TYPES_PART in parts:
            parts[_CONTENT_TYPES_PART] = _drop_content_type_overrides(
                parts[_CONTENT_TYPES_PART], dropped
            )
        # rels Targets are relative to the directory of the part they describe:
        # word/_rels/*.rels → word/ ; _rels/.rels → package root. Clean every
        # kept rels file so no relationship points at a dropped part.
        for info in kept_infos:
            name = info.filename
            if not name.endswith(".rels"):
                continue
            directory = name.rpartition("/_rels/")[0]
            parts[name] = _drop_relationships(parts[name], directory, dropped)
    except ET.ParseError as exc:
        raise QuartoScaffoldError(f"{src} has a malformed XML part: {exc}") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".reference-", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in kept_infos:
                zout.writestr(info, parts[info.filename])
        os.replace(tmp_path, dest)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _scrub_core_props(core_xml: bytes) -> bytes:
    """Blank authorship/metadata text in ``docProps/core.xml`` (keep the part).

    Empties every leaf element's text (``dc:creator``, ``cp:lastModifiedBy``,
    ``dc:title``/``subject``/``description``, ``cp:keywords``, dates), so no PHI
    survives while the part stays structurally valid.
    """
    root = _parse_preserving_ns(core_xml)
    for el in root.iter():
        if list(el):
            continue  # container element — only blank leaf text
        el.text = None
    result = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result


def _scrub_settings(settings_xml: bytes) -> bytes:
    """Scrub PHI from ``word/settings.xml`` while keeping style-relevant settings.

    Removes any ``<w:mailMerge>`` element (its ``<w:odso>``/``<w:connectString>``
    carry a DB connection string) and strips author-bearing attributes
    (``w:author`` and any attribute whose local-name contains ``lastModifiedBy``)
    anywhere in the tree. Everything else (zoom, defaults, compat flags, …) is
    kept.
    """
    root = _parse_preserving_ns(settings_xml)
    mail_merge_tag = f"{{{_W_NS}}}mailMerge"
    author_attr = f"{{{_W_NS}}}author"
    for parent in root.iter():
        for child in list(parent):
            if child.tag == mail_merge_tag:
                parent.remove(child)
    for el in root.iter():
        for attr in list(el.attrib):
            local = attr.rpartition("}")[2]
            if attr == author_attr or "lastModifiedBy" in local:
                del el.attrib[attr]
    result = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result


def _drop_content_type_overrides(content_types_xml: bytes, dropped: set[str]) -> bytes:
    """Remove ``<Override>`` entries for ``dropped`` parts from ``[Content_Types]``.

    ``dropped`` holds zip part names (``word/footnotes.xml``); Override
    ``PartName`` is the same path with a leading slash (``/word/footnotes.xml``).
    """
    targets = {f"/{name}" for name in dropped}
    root = _parse_preserving_ns(content_types_xml)
    for override in root.findall(f"{{{_CT_NS}}}Override"):
        if override.get("PartName") in targets:
            root.remove(override)
    result = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result


def _drop_relationships(rels_xml: bytes, base_dir: str, dropped: set[str]) -> bytes:
    """Remove ``<Relationship>`` entries whose Target resolves to a dropped part.

    ``base_dir`` is the directory of the part the rels file describes (``"word"``
    for ``word/_rels/document.xml.rels``, ``""`` for the package-root
    ``_rels/.rels``). A relative internal ``Target`` is joined onto it and
    matched against ``dropped`` (a set of full zip part names). External targets
    (``TargetMode="External"``, e.g. hyperlinks) are left untouched.
    """
    root = _parse_preserving_ns(rels_xml)
    for rel in root.findall(f"{{{_PKG_REL_NS}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = (rel.get("Target") or "").lstrip("/")
        if not target:
            continue
        resolved = f"{base_dir}/{target}" if base_dir else target
        if resolved in dropped:
            root.remove(rel)
    result = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    assert isinstance(result, bytes)
    return result


def _parse_preserving_ns(xml: bytes) -> ET.Element:
    """Parse ``xml`` after registering every namespace prefix it declares.

    ElementTree otherwise re-serializes unregistered prefixes as ``ns0``/``ns1``
    (corrupting ``mc:Ignorable``, the default OPC namespace of
    ``[Content_Types].xml``/rels, ``cp:``/``dc:`` in core props, …). Harvesting
    ``start-ns`` events — including the empty-string default prefix — keeps the
    output byte-faithful in prefixes. See commit b4ad26f.
    """
    for _event, ns in ET.iterparse(io.BytesIO(xml), events=["start-ns"]):
        ET.register_namespace(str(ns[0]), str(ns[1]))
    return ET.fromstring(xml)


def _empty_body(document_xml: bytes) -> bytes:
    """Return ``document_xml`` with ``<w:body>`` reduced to its ``<w:sectPr>``.

    Fails SAFE: a ``document.xml`` without a ``<w:body>`` is malformed; passing
    it through unchanged could leak body text, so raise instead.

    Raises:
        QuartoScaffoldError: no ``<w:body>`` element found.
    """
    root = _parse_preserving_ns(document_xml)
    body = root.find(f"{{{_W_NS}}}body")
    if body is None:
        raise QuartoScaffoldError("malformed document.xml: no <w:body> element")
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
        QuartoScaffoldError: the subprocess exits non-zero or emits no bytes.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [str(quarto_executable), "pandoc", "--print-default-data-file", "reference.docx"],
            capture_output=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise QuartoScaffoldError(
            "failed to generate default reference.docx: pandoc timed out after 60 s"
        ) from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()[:500]
        raise QuartoScaffoldError(f"failed to generate default reference.docx: {detail}")
    if not proc.stdout:
        raise QuartoScaffoldError(
            "failed to generate default reference.docx: pandoc produced no output"
        )
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
    if not _VALID_NAME_RE.match(name):
        raise QuartoScaffoldError(
            f"invalid report name {name!r}: use only letters, digits, '.', '_', '-'"
        )
    if name.startswith("."):
        raise QuartoScaffoldError(f"invalid report name {name!r}: must not start with '.'")
    if name.lower().endswith(".qmd"):
        raise QuartoScaffoldError(
            f"invalid report name {name!r}: drop the '.qmd' suffix (it is added automatically)"
        )

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
