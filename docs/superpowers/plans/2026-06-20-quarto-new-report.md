# `clarinet quarto new` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `clarinet quarto new <name>` CLI subcommand that scaffolds a minimal Quarto report (`<name>.qmd`) plus its `reference.docx` style asset into the project's reports folder.

**Architecture:** Pure-logic module `clarinet/utils/quarto_scaffold.py` (symmetric to `quarto_discovery.py`, which reads `.qmd` — this one writes), driven by a thin argparse subcommand in `cli/main.py`. Front matter is built as a dict and serialized with `yaml.safe_dump`. The `reference.docx` is either generated default (bundled pandoc via the installed Quarto) or, with `--from-docx`, derived from a user Word file by **emptying its document body** (keeps styles/theme/numbering/headers, drops body text — a PHI-leak guard, since `review/reference.docx` is committed and shipped in the deploy bundle).

**Tech Stack:** Python 3.12, argparse, `pyyaml` (already a base dep), stdlib `zipfile` + `xml.etree.ElementTree`, the bundled Quarto/pandoc CLI, pytest.

## Global Constraints

- All Python tools run via `uv run` (e.g. `uv run pytest ...`); quality gate is `make check`.
- **No new dependencies.** Body-emptying uses stdlib `zipfile` + `xml.etree.ElementTree`; YAML uses the existing `pyyaml`.
- Logger only: `from clarinet.utils.logger import logger`. CLI user-facing success/error output uses `logger.info` / `logger.error` (mirrors `copy_template` / `install_quarto`); never `print()` for diagnostics.
- `reference.docx` is **one per folder** — the render service stages exactly `qmd_path.parent / "reference.docx"` (filename hardcoded). The command never writes a differently-named reference doc.
- `--from-docx` must **not** copy body content; it physically empties `word/document.xml`'s `<w:body>` (keeping the trailing `<w:sectPr>`).
- Scaffold logic is **synchronous**; the CLI calls it directly (like `install_quarto`), not via `asyncio.run`.
- Typed exceptions: new `QuartoScaffoldError(ClarinetError)`; reuse existing `QuartoNotInstalledError` for a missing Quarto binary.
- `make check` (ruff format + ruff check + mypy) must pass; mypy is strict.

## File Structure

| File | Responsibility |
|---|---|
| `clarinet/utils/quarto_scaffold.py` (create) | All scaffold logic: `build_qmd_text`, `strip_docx_body` (+ `_empty_body`), `generate_default_reference`, `scaffold_quarto_report`. |
| `clarinet/exceptions/domain.py` (modify, ~line 400 after `QuartoRenderError`) | Add `QuartoScaffoldError(ClarinetError)`. |
| `clarinet/cli/main.py` (modify: parser ~line 1468 after `gen-types`; dispatch ~line 1145 in `handle_quarto_command`; helper near other quarto fns) | `new` subparser, dispatch branch, `cmd_quarto_new(args)` helper. |
| `tests/test_quarto_scaffold.py` (create) | Unit tests for all four functions + CLI dispatch, with a programmatic `_make_docx` fixture. |
| `docs/quarto-reports.md` (modify) + `CLAUDE.md` (modify: Essential Commands → CLI list) | Document the new subcommand. |

**Decisions locked in (from brainstorming):** subcommand name `new`; `--lang` default `ru`; `--format` default `docx`; minimal `.qmd` body (front matter + one empty heading); `--from-docx` = "whole letterhead minus body text" (keep headers/footers/theme/numbering, drop body text).

---

### Task 1: Front matter builder (`build_qmd_text`)

**Files:**
- Create: `clarinet/utils/quarto_scaffold.py`
- Test: `tests/test_quarto_scaffold.py`

**Interfaces:**
- Produces: `build_qmd_text(*, title: str, description: str, lang: str, formats: list[str], data_reports: list[str], reference_doc: str | None) -> str` — returns the full `.qmd` text (YAML front matter fenced by `---` + a trailing empty `# ` heading). `formats` is a subset of `{"docx", "pdf"}`. `reference_doc` (e.g. `"reference.docx"` or `None`) is emitted under `format.docx.reference-doc` only when present.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quarto_scaffold.py
"""Unit tests for clarinet.utils.quarto_scaffold."""

import io
import zipfile
from pathlib import Path

import pytest
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
        title="t", description="", lang="ru",
        formats=["docx"], data_reports=[], reference_doc="reference.docx",
    )
    assert "clarinet" not in _front_matter(qmd)


def test_build_qmd_no_reference_doc_when_none() -> None:
    qmd = build_qmd_text(
        title="t", description="", lang="ru",
        formats=["docx"], data_reports=[], reference_doc=None,
    )
    assert "reference-doc" not in qmd


def test_build_qmd_both_formats() -> None:
    qmd = build_qmd_text(
        title="t", description="", lang="en",
        formats=["docx", "pdf"], data_reports=[], reference_doc="reference.docx",
    )
    fm = _front_matter(qmd)
    assert "docx" in fm["format"]
    assert "pdf" in fm["format"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_quarto_scaffold.py -k build_qmd -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'clarinet.utils.quarto_scaffold'`.

- [ ] **Step 3: Write the minimal implementation**

```python
# clarinet/utils/quarto_scaffold.py
"""Scaffolding for new Quarto reports (``clarinet quarto new``).

Symmetric to :mod:`clarinet.utils.quarto_discovery` (that module reads ``.qmd``
front matter; this one writes a fresh ``.qmd`` plus its sibling
``reference.docx`` style asset). Pure file/CLI logic — no DB, no app state.
"""

import yaml


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_quarto_scaffold.py -k build_qmd -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add clarinet/utils/quarto_scaffold.py tests/test_quarto_scaffold.py
git commit -m "feat(quarto): build_qmd_text front-matter generator for report scaffolding"
```

---

### Task 2: Empty docx body (`strip_docx_body`) + `QuartoScaffoldError`

**Files:**
- Modify: `clarinet/exceptions/domain.py` (after `QuartoRenderError`, ~line 400)
- Modify: `clarinet/utils/quarto_scaffold.py`
- Test: `tests/test_quarto_scaffold.py`

**Interfaces:**
- Produces: `strip_docx_body(src: Path, dest: Path) -> None` — copies the `src` docx to `dest` with `word/document.xml`'s `<w:body>` emptied (keeps a trailing `<w:sectPr>`); all other zip parts (`styles.xml`, `theme*`, `numbering.xml`, `header*.xml`/`footer*.xml`, `media/`, `docProps`, `_rels`) copied byte-for-byte. Raises `QuartoScaffoldError` when `src` is not a zip or lacks `word/document.xml`.
- Produces: `QuartoScaffoldError(ClarinetError)` in `clarinet/exceptions/domain.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quarto_scaffold.py  (append)
from clarinet.exceptions.domain import QuartoScaffoldError
from clarinet.utils.quarto_scaffold import strip_docx_body

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_quarto_scaffold.py -k strip -v`
Expected: FAIL — `ImportError: cannot import name 'QuartoScaffoldError'` / `strip_docx_body`.

- [ ] **Step 3: Write the implementation**

Add the exception in `clarinet/exceptions/domain.py` right after `QuartoRenderError`:

```python
class QuartoScaffoldError(ClarinetError):
    """Raised when scaffolding a new Quarto report fails: an invalid report
    name, an existing target file without ``--force``, or an unreadable
    source ``.docx`` passed to ``--from-docx``."""
```

Add to `clarinet/utils/quarto_scaffold.py` (extend the import block + new code):

```python
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from clarinet.exceptions.domain import QuartoScaffoldError

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_DOCUMENT_PART = "word/document.xml"


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
                raise QuartoScaffoldError(
                    f"{src} is not a valid .docx (missing {_DOCUMENT_PART})"
                )
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
    return ET.tostring(root, encoding="UTF-8", xml_declaration=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_quarto_scaffold.py -k strip -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add clarinet/exceptions/domain.py clarinet/utils/quarto_scaffold.py tests/test_quarto_scaffold.py
git commit -m "feat(quarto): strip_docx_body to derive reference.docx without body text"
```

---

### Task 3: Default reference.docx (`generate_default_reference`)

**Files:**
- Modify: `clarinet/utils/quarto_scaffold.py`
- Test: `tests/test_quarto_scaffold.py`

**Interfaces:**
- Produces: `generate_default_reference(dest: Path, quarto_executable: Path) -> None` — runs `<quarto> pandoc --print-default-data-file reference.docx`, writes stdout bytes to `dest`. Raises `QuartoScaffoldError` on non-zero exit. The caller is responsible for resolving `quarto_executable` first.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quarto_scaffold.py  (append)
import subprocess

from clarinet.utils.quarto_scaffold import generate_default_reference


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_quarto_scaffold.py -k generate_default -v`
Expected: FAIL — `ImportError: cannot import name 'generate_default_reference'`.

- [ ] **Step 3: Write the implementation**

Add to `clarinet/utils/quarto_scaffold.py` (add `import subprocess` to the import block):

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_quarto_scaffold.py -k generate_default -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add clarinet/utils/quarto_scaffold.py tests/test_quarto_scaffold.py
git commit -m "feat(quarto): generate_default_reference via bundled pandoc"
```

---

### Task 4: Orchestrator (`scaffold_quarto_report`)

**Files:**
- Modify: `clarinet/utils/quarto_scaffold.py`
- Test: `tests/test_quarto_scaffold.py`

**Interfaces:**
- Consumes: `build_qmd_text`, `strip_docx_body`, `generate_default_reference` (Tasks 1–3); `resolve_quarto_executable` from `clarinet.services.quarto_render`; `settings.get_quarto_reports_path()`.
- Produces: `scaffold_quarto_report(name, *, title=None, description="", lang="ru", formats, data_reports, from_docx=None, force=False, reports_dir=None) -> Path` — writes `<name>.qmd` (+ `reference.docx` when `docx` in `formats`) into `reports_dir` (default `settings.get_quarto_reports_path()`), returns the `.qmd` path. `formats: list[str]`, `from_docx: Path | None`, `reports_dir: Path | None`. Raises `QuartoScaffoldError` (bad name / existing file without `force`) or `QuartoNotInstalledError` (default reference needs Quarto, none installed).

**Behavior contract (encode exactly):**
1. `name` with `/`, `\`, `..`, or empty → `QuartoScaffoldError`.
2. `reports_dir` defaults to `settings.get_quarto_reports_path()`; created if missing.
3. `<name>.qmd` exists and not `force` → `QuartoScaffoldError`.
4. reference.docx (always `reports_dir / "reference.docx"`), only when `docx in formats`:
   - `from_docx` set: validate it exists and ends `.docx` (else `QuartoScaffoldError`); if reference.docx exists and not `force` → `QuartoScaffoldError`; else `strip_docx_body`.
   - no `from_docx`: if reference.docx already exists → keep it (log); else resolve Quarto (`None` → `QuartoNotInstalledError`) and `generate_default_reference`.
   - `reference_doc_name = "reference.docx"`.
5. `docx not in formats` (pdf-only): `reference_doc_name = None`; if `from_docx` was given, `logger.warning` that reference applies to docx only.
6. `title = title or name`; write `build_qmd_text(...)`; return qmd path.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_quarto_scaffold.py  (append)
from clarinet.exceptions.domain import QuartoNotInstalledError
from clarinet.utils.quarto_scaffold import scaffold_quarto_report

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
        "summary", title="Сводка", formats=["docx"], data_reports=["demo_records"],
        reports_dir=tmp_path,
    )
    assert qmd == tmp_path / "summary.qmd"
    assert qmd.read_text(encoding="utf-8").count("reference.docx") >= 1
    assert (tmp_path / "reference.docx").read_bytes() == b"PKref"


def test_scaffold_from_docx_uses_strip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    src = _make_docx(tmp_path / "brand.docx")
    qmd = scaffold_quarto_report(
        "rep", formats=["docx"], data_reports=[], from_docx=src, reports_dir=tmp_path,
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_quarto_scaffold.py -k scaffold -v`
Expected: FAIL — `ImportError: cannot import name 'scaffold_quarto_report'`.

- [ ] **Step 3: Write the implementation**

Add to `clarinet/utils/quarto_scaffold.py` (extend imports: `from clarinet.exceptions.domain import QuartoNotInstalledError, QuartoScaffoldError`; `from clarinet.services.quarto_render import resolve_quarto_executable`; `from clarinet.settings import settings`; `from clarinet.utils.logger import logger`):

```python
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
            "default reference.docx needs Quarto; run 'clarinet quarto install' "
            "or pass --from-docx"
        )
    generate_default_reference(ref_path, executable)
    return "reference.docx"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_quarto_scaffold.py -k scaffold -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add clarinet/utils/quarto_scaffold.py tests/test_quarto_scaffold.py
git commit -m "feat(quarto): scaffold_quarto_report orchestrator"
```

---

### Task 5: CLI wiring + docs

**Files:**
- Modify: `clarinet/cli/main.py` (parser ~line 1468 after `gen-types`; dispatch ~line 1145 in `handle_quarto_command`; add `cmd_quarto_new` helper near `generate_report_types`)
- Modify: `docs/quarto-reports.md`, `CLAUDE.md`
- Test: `tests/test_quarto_scaffold.py`

**Interfaces:**
- Consumes: `scaffold_quarto_report` (Task 4).
- Produces: `cmd_quarto_new(args: argparse.Namespace) -> None` — maps `--format` (`docx`/`pdf`/`both`) to a `list[str]`, splits `--data` on commas, builds `from_docx: Path | None`, calls `scaffold_quarto_report`, and on `QuartoScaffoldError`/`QuartoNotInstalledError` logs and `sys.exit(1)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_quarto_scaffold.py  (append)
import argparse

from clarinet.cli.main import cmd_quarto_new


def test_cmd_quarto_new_maps_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    def fake_scaffold(name: str, **kwargs: object) -> Path:
        seen["name"] = name
        seen.update(kwargs)
        return tmp_path / f"{name}.qmd"

    monkeypatch.setattr("clarinet.cli.main.scaffold_quarto_report", fake_scaffold)
    args = argparse.Namespace(
        name="rep", title="T", description="", lang="ru",
        format="both", data="a, b", from_docx=None, force=False,
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
        name="rep", title=None, description="", lang="ru",
        format="docx", data="", from_docx=None, force=False,
    )
    with pytest.raises(SystemExit):
        cmd_quarto_new(args)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_quarto_scaffold.py -k cmd_quarto_new -v`
Expected: FAIL — `ImportError: cannot import name 'cmd_quarto_new'`.

- [ ] **Step 3: Write the implementation**

Add the helper in `clarinet/cli/main.py` near `generate_report_types` (module-level import at top: `from clarinet.utils.quarto_scaffold import scaffold_quarto_report`):

```python
def cmd_quarto_new(args: argparse.Namespace) -> None:
    """Handle ``clarinet quarto new`` — scaffold a .qmd + reference.docx."""
    from clarinet.exceptions.domain import QuartoNotInstalledError, QuartoScaffoldError

    formats = {"docx": ["docx"], "pdf": ["pdf"], "both": ["docx", "pdf"]}[args.format]
    data_reports = [item.strip() for item in args.data.split(",") if item.strip()]
    from_docx = Path(args.from_docx) if args.from_docx else None
    try:
        scaffold_quarto_report(
            args.name,
            title=args.title,
            description=args.description,
            lang=args.lang,
            formats=formats,
            data_reports=data_reports,
            from_docx=from_docx,
            force=args.force,
        )
    except (QuartoScaffoldError, QuartoNotInstalledError) as exc:
        logger.error(str(exc))
        sys.exit(1)
```

Add the dispatch branch in `handle_quarto_command` (after the `gen-types` branch, ~line 1146):

```python
    elif args.quarto_command == "new":
        cmd_quarto_new(args)
```

Add the subparser after the `gen-types` parser (~line 1472):

```python
    quarto_new_parser = quarto_subparsers.add_parser(
        "new", help="Scaffold a new Quarto report (.qmd + reference.docx)"
    )
    quarto_new_parser.add_argument("name", help="Report name → <name>.qmd")
    quarto_new_parser.add_argument("--title", help="Front-matter title (default: <name>)")
    quarto_new_parser.add_argument("--description", default="", help="Front-matter description")
    quarto_new_parser.add_argument("--lang", default="ru", help="Document language (default: ru)")
    quarto_new_parser.add_argument(
        "--format", default="docx", choices=["docx", "pdf", "both"], help="Output format(s)"
    )
    quarto_new_parser.add_argument(
        "--data", default="", help="Comma-separated SQL report names for clarinet.data"
    )
    quarto_new_parser.add_argument(
        "--from-docx", help="Existing .docx whose styles become reference.docx"
    )
    quarto_new_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing .qmd / reference.docx"
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_quarto_scaffold.py -k cmd_quarto_new -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Update docs**

In `docs/quarto-reports.md`, add a short "Scaffolding a new report" section documenting `clarinet quarto new <name> [--title --description --lang --format --data --from-docx --force]`, noting: minimal `.qmd`, one shared `reference.docx` per folder, `--from-docx` copies styles only (body text dropped), default reference needs Quarto installed.

In `CLAUDE.md` under **Essential Commands → Operations (CLI)**, add a line to the quarto group:
```
uv run clarinet quarto new NAME             # Scaffold a Quarto report (.qmd + reference.docx)
```

- [ ] **Step 6: Full check + commit**

Run: `make check > /tmp/check-quarto-new-report.txt 2>&1` (then read the file)
Expected: format/lint/typecheck pass.

Run: `uv run pytest tests/test_quarto_scaffold.py -v > /tmp/test-quarto-new-report.txt 2>&1` (then read the file)
Expected: all pass (~19 tests).

```bash
git add clarinet/cli/main.py docs/quarto-reports.md CLAUDE.md tests/test_quarto_scaffold.py
git commit -m "feat(quarto): wire 'clarinet quarto new' CLI subcommand + docs"
```

---

## Self-Review

**1. Spec coverage:**
- CLI `clarinet quarto new <name>` + all flags → Task 5. ✓
- Minimal `.qmd` (front matter + empty heading) → Task 1. ✓
- `--from-docx` = styles only, body dropped (PHI guard) → Task 2. ✓
- Default reference.docx via bundled pandoc → Task 3. ✓
- One reference.docx per folder; keep existing; `--force` to replace → Task 4 (`_prepare_reference`). ✓
- Error handling (bad name, existing file, missing Quarto, bad docx, pdf-only) → Tasks 2/4/5. ✓
- `--from-docx` works without Quarto → Task 4 (only the default branch resolves Quarto). ✓

**2. Placeholder scan:** No TBD/TODO; every code step has complete code; tests are concrete. ✓

**3. Type consistency:** `build_qmd_text(reference_doc: str | None)`, `strip_docx_body(src, dest) -> None`, `generate_default_reference(dest, quarto_executable) -> None`, `scaffold_quarto_report(...) -> Path`, `_prepare_reference(...) -> str | None`, `cmd_quarto_new(args) -> None` — names/signatures match across tasks. `resolve_quarto_executable` is imported into `quarto_scaffold` and patched in tests at `clarinet.utils.quarto_scaffold.resolve_quarto_executable`. ✓

**Risk note (verify during Task 2):** `xml.etree.ElementTree` re-serialization registers only `w`/`r` namespaces. After body emptying the residual XML uses only those, so typical Word/pandoc docx round-trip cleanly; if a real-world `--from-docx` ever produces a doc Quarto rejects, add an integration test rendering with a real reference.docx (skipif no Quarto) and, if needed, preserve the original root `xmlns` declarations verbatim.
