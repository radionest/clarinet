# Files Facade Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the 10-module path/file machinery into one public `Files` facade in `clarinet/files/`, with all internals private behind `_`-prefixed modules, unifying the two `{placeholder}` engines onto one renderer + one field-source builder.

**Architecture:** Build the new `clarinet/files/` package *alongside* the existing modules (both compile and pass tests), migrate every call-site from the old imports to `Files`, then delete the old modules in one final task. `Files` absorbs `FileResolver` (instance methods) + `FileRepository`; the working-dir builders stay as private functions in `_resolver.py`; rendering goes through one `_template.render` fed by one `_patterns.fields_from`.

**Tech Stack:** Python 3.12+, SQLModel/Pydantic DTOs, pytest, `uv run` for all tooling, ruff + mypy via `make check`.

**Spec:** `docs/superpowers/specs/2026-06-19-files-facade-design.md` (read it before starting).

## Global Constraints

- Run all Python tooling through `uv run` (e.g. `uv run pytest ...`); never bare `pytest`.
- Logger: `from clarinet.utils.logger import logger` — never import loguru directly.
- Settings: `from clarinet.settings import settings`.
- Custom exceptions: domain layer (`clarinet.exceptions.domain`) inside `clarinet/files/`; never `clarinet.exceptions.http`.
- Clean break — **no** backward-compat aliases for `FileResolver` / `FileRepository` / the moved functions.
- The public surface is exactly one name: `from clarinet.files import Files`. Everything else is `_`-private.
- Commit after every task with a conventional-commit message (no `Co-Authored-By` trailer).
- Redirect long test runs to a unique file: `uv run pytest ... > /tmp/test-files-facade.txt 2>&1` (never pipe).

---

## Prerequisite — rebase the worktree onto `origin/main`

The worktree `worktree-files-facade` was branched **before** PR #387 merged; its base is 22 commits behind `origin/main`, and #387 (`FileResolver.from_record` / `ctx.files_for`, commit `c75a28c`) is part of the baseline this plan assumes. The local `main` is also stale — rebase onto **`origin/main`**, not local `main`.

- [ ] **Step 1: Fetch and rebase**

```bash
git -C /home/nest/clarinet/.claude/worktrees/files-facade fetch origin main
git -C /home/nest/clarinet/.claude/worktrees/files-facade rebase origin/main
```
Expected: the single spec/plan commit replays cleanly onto `origin/main` (new files, no conflicts).

- [ ] **Step 2: Confirm #387 is now in the worktree**

```bash
grep -n "def from_record" clarinet/services/common/file_resolver.py
grep -n "def files_for"  clarinet/services/pipeline/context.py
```
Expected: both found.

---

## File Structure

New package (created in Phase A, populated alongside the old modules):

```
clarinet/files/
  __init__.py     # PUBLIC: lazy __getattr__ exposing only Files + AnonPathError. No heavy top-level imports.
  facade.py       # Files — the one public class (absorbs FileResolver instance API + FileRepository)
  _template.py    # ← copy of utils/path_template.py (stdlib-only renderer: render_template, validate_template, RenderMode, coerce_field_value, SUPPORTED_PLACEHOLDERS, extract_placeholders)
  _anon.py        # ← copy of utils/anon_resolve.py (require_anon_or_raw)
  _fs.py          # ← copy of utils/fs.py (run_in_fs_thread, shutdown_fs_executor)
  _storage.py     # ← copy of services/common/storage_paths.py (render_all_levels, build_context, render_working_folder, derive_anon_patient_id, compute_display_anon_id, split_template, validate_template re-export)
  _resolver.py    # ← build_working_dirs* + lazy snapshots extracted from services/common/file_resolver.py (NO FileResolver class)
  _patterns.py    # ← glob_file_paths + resolve_origin_type from utils/file_patterns.py + NEW fields_from; resolve_pattern / resolve_record_field / _VIRTUAL_FIELD_MAP deleted
  _checksums.py   # ← _sha256 + compute_file_checksum + checksums_changed from utils/file_checksums.py (compute_checksums removed — moves to Files.checksums)
```

Deleted in Phase D: `repositories/file_repository.py`, `services/common/file_resolver.py`, `services/common/storage_paths.py`, `utils/path_template.py`, `utils/anon_resolve.py`, `utils/file_patterns.py`, `utils/file_checksums.py`, `utils/fs.py`.

Kept (out of scope): `utils/file_registry_resolver.py`, `utils/file_link_sync.py`.

New test files: `tests/test_files_facade.py` (Files behavior), `tests/test_files_fields_from.py` (engine unification). Existing tests migrate in Task 22.

---

## Phase A — Build the private leaf modules (coexist with old)

### Task 1: Package skeleton + pure-copy leaves (`_template`, `_anon`, `_fs`)

**Files:**
- Create: `clarinet/files/__init__.py` (temporary placeholder, finalized in Task 12), `clarinet/files/_template.py`, `clarinet/files/_anon.py`, `clarinet/files/_fs.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Produces: `clarinet.files._template.{render_template, validate_template, RenderMode, coerce_field_value, extract_placeholders, SUPPORTED_PLACEHOLDERS}`; `clarinet.files._anon.require_anon_or_raw`; `clarinet.files._fs.{run_in_fs_thread, shutdown_fs_executor}`.

- [ ] **Step 1: Create the package init (placeholder)**

```python
# clarinet/files/__init__.py
"""Public facade for on-disk path resolution and file access (finalized in Task 12)."""
```

- [ ] **Step 2: Copy the three leaf modules verbatim**

These three modules import only stdlib / `clarinet.utils.logger` / `clarinet.exceptions` / `clarinet.models.base` — no intra-group imports — so they copy with **no edits**.

```bash
cp clarinet/utils/path_template.py clarinet/files/_template.py
cp clarinet/utils/anon_resolve.py  clarinet/files/_anon.py
cp clarinet/utils/fs.py            clarinet/files/_fs.py
```

- [ ] **Step 3: Write the failing import test**

```python
# tests/test_files_facade.py
def test_leaf_modules_import():
    from clarinet.files._template import render_template, validate_template, RenderMode
    from clarinet.files._anon import require_anon_or_raw
    from clarinet.files._fs import run_in_fs_thread, shutdown_fs_executor

    assert render_template("{a}", {"a": "x"}) == "x"
    assert validate_template("{patient_id}/{study_uid}/{series_uid}")
```

- [ ] **Step 4: Run it**

Run: `uv run pytest tests/test_files_facade.py::test_leaf_modules_import -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/__init__.py clarinet/files/_template.py clarinet/files/_anon.py clarinet/files/_fs.py tests/test_files_facade.py
git commit -m "refactor(files): add files package skeleton + stdlib leaf modules"
```

### Task 2: `_storage.py` (copy + repoint two imports)

**Files:**
- Create: `clarinet/files/_storage.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Consumes: `_template`, `_anon` (Task 1).
- Produces: `clarinet.files._storage.{render_all_levels, render_working_folder, build_context, derive_anon_patient_id, compute_display_anon_id, split_template, TemplateSegments}`.

- [ ] **Step 1: Copy and repoint**

```bash
cp clarinet/services/common/storage_paths.py clarinet/files/_storage.py
```
Then edit the two intra-group imports in `clarinet/files/_storage.py`:
- `from clarinet.utils.anon_resolve import require_anon_or_raw` → `from clarinet.files._anon import require_anon_or_raw`
- `from clarinet.utils.path_template import (` → `from clarinet.files._template import (`

(All other imports — `clarinet.exceptions.domain`, `clarinet.models.base`, `clarinet.services.dicom.models`, `clarinet.settings` — stay unchanged.)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_files_facade.py
def test_storage_render_all_levels_smoke(monkeypatch):
    from pathlib import Path
    from unittest.mock import MagicMock
    from clarinet.models.base import DicomQueryLevel
    from clarinet.files import _storage

    patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    dirs = _storage.render_all_levels(
        patient=patient, study=None, series=None,
        storage_path=Path("/data"), template="{anon_patient_id}/{study_uid}/{series_uid}",
    )
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py::test_storage_render_all_levels_smoke -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/_storage.py tests/test_files_facade.py
git commit -m "refactor(files): move storage-path rendering to files/_storage"
```

### Task 3: `_resolver.py` (working-dir builders, no class)

**Files:**
- Create: `clarinet/files/_resolver.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Consumes: `_storage.render_all_levels` (Task 2).
- Produces: `clarinet.files._resolver.{build_working_dirs, build_working_dirs_from_series, build_working_dirs_from_study, build_working_dirs_from_patient}` (module-level functions) + the `_StudyLazySnapshot` / `_SeriesLazySnapshot` dataclasses.

- [ ] **Step 1: Create `_resolver.py` from the FileResolver static factories**

Copy *only* these pieces out of `clarinet/services/common/file_resolver.py`, as **module-level functions** (drop the `@staticmethod` decorator and the `class FileResolver:` wrapper; de-indent):
- `_StudyLazySnapshot`, `_SeriesLazySnapshot` dataclasses (verbatim)
- `build_working_dirs(record, *, fallback_to_unanonymized=False)` (was `FileResolver.build_working_dirs`)
- `build_working_dirs_from_series(...)`, `build_working_dirs_from_study(...)`, `build_working_dirs_from_patient(...)`

Repoint the import: `from clarinet.services.common.storage_paths import render_all_levels` → `from clarinet.files._storage import render_all_levels`. Keep the `TYPE_CHECKING` model imports and `from clarinet.settings import settings`. Do **not** copy `FileResolver.__init__`, `resolve`, `exists`, `glob`, `dir`, `_lookup`, `build_fields`, `snapshot_checksums`, `accessed_files`, or `resolve_pattern_from_dict` — those move to `facade.py` / are deleted.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_files_facade.py
def test_resolver_build_working_dirs(monkeypatch):
    from pathlib import Path
    from unittest.mock import MagicMock
    from clarinet.models.base import DicomQueryLevel
    from clarinet.files import _resolver
    monkeypatch.setattr("clarinet.files._resolver.settings", MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"))

    record = MagicMock()
    record.clarinet_storage_path = None
    record.patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    record.study = None; record.study_uid = None
    record.series = None; record.series_uid = None
    dirs = _resolver.build_working_dirs(record)
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py::test_resolver_build_working_dirs -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/_resolver.py tests/test_files_facade.py
git commit -m "refactor(files): extract working-dir builders into files/_resolver"
```

### Task 4: `_patterns.py` + the unified `fields_from` (NOVEL)

**Files:**
- Create: `clarinet/files/_patterns.py`
- Test: `tests/test_files_fields_from.py`

**Interfaces:**
- Produces: `clarinet.files._patterns.{glob_file_paths, resolve_origin_type, fields_from, PLACEHOLDER_REGEX}`.
- `fields_from(record, parent=None) -> dict[str, Any]` — the canonical placeholder dict.

- [ ] **Step 1: Create `_patterns.py`**

Copy from `clarinet/utils/file_patterns.py` only `PLACEHOLDER_REGEX`, the `_PatternedFile` Protocol, `resolve_origin_type`, and `glob_file_paths` (verbatim). Do **not** copy `resolve_pattern`, `resolve_record_field`, or `_VIRTUAL_FIELD_MAP`. Then add `fields_from`:

```python
# clarinet/files/_patterns.py — append after resolve_origin_type
from typing import Any


def fields_from(record: RecordRead, parent: RecordRead | None = None) -> dict[str, Any]:
    """Canonical placeholder dict for a record.

    Unifies the three legacy field sources (``FileResolver.build_fields``,
    ``resolve_record_field``'s fallback chain, and the slicer / pipeline
    manual ``origin_type`` patch). Scalar placeholders fall back to *parent*
    when the record's own value is missing/empty; ``origin_type`` uses the
    inverted virtual-field priority via :func:`resolve_origin_type`; the
    ``data`` sub-dict is parent-then-record merged for ``{data.FIELD}`` access.
    Coercion (lists → ``"CT_SR"``) happens later in ``_template.render``.
    """

    def scalar(name: str) -> Any:
        value = getattr(record, name, None)
        if value in (None, "") and parent is not None:
            value = getattr(parent, name, None)
        return value

    data = {**(getattr(parent, "data", None) or {}), **(getattr(record, "data", None) or {})}
    return {
        "id": record.id,
        "user_id": scalar("user_id"),
        "patient_id": scalar("patient_id"),
        "study_uid": scalar("study_uid"),
        "series_uid": scalar("series_uid"),
        "record_type": {"name": record.record_type.name},
        "data": data,
        "origin_type": resolve_origin_type(record, parent),
    }
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_files_fields_from.py
from unittest.mock import MagicMock
from clarinet.files._patterns import fields_from
from clarinet.files._template import render_template, RenderMode


def _rec(*, rid=1, user_id=None, rtype="seg", data=None):
    r = MagicMock()
    r.id = rid; r.user_id = user_id
    r.patient_id = "P1"; r.study_uid = "S"; r.series_uid = "SE"
    r.record_type = MagicMock(name_attr=rtype); r.record_type.name = rtype
    r.data = data or {}
    return r


def test_origin_type_inverts_to_parent():
    child = _rec(rtype="compare")
    parent = _rec(rtype="segmentation")
    assert fields_from(child, parent)["origin_type"] == "segmentation"
    assert fields_from(child)["origin_type"] == "compare"


def test_scalar_falls_back_to_parent():
    child = _rec(user_id=None)
    parent = _rec(user_id="doctor-7")
    assert fields_from(child, parent)["user_id"] == "doctor-7"
    assert fields_from(child)["user_id"] is None


def test_list_data_field_coerces_join_not_repr():
    rec = _rec(data={"mods": ["SR", "CT"]})
    out = render_template("{data.mods}", fields_from(rec), mode=RenderMode.LENIENT)
    assert out == "CT_SR"  # sorted, "_"-joined — NOT "['SR', 'CT']"
```

- [ ] **Step 3: Run them — verify they fail**

Run: `uv run pytest tests/test_files_fields_from.py -v`
Expected: FAIL (`fields_from` not yet importable / behavior absent) until Step 1 is in place; then PASS.

- [ ] **Step 4: Run them — verify they pass**

Run: `uv run pytest tests/test_files_fields_from.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/_patterns.py tests/test_files_fields_from.py
git commit -m "refactor(files): unify field-source into files/_patterns.fields_from"
```

### Task 5: `_checksums.py` (sha256 + change detection only)

**Files:**
- Create: `clarinet/files/_checksums.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Consumes: `_fs.run_in_fs_thread` (Task 1).
- Produces: `clarinet.files._checksums.{compute_file_checksum, checksums_changed}` (+ private `_sha256`, `_sha256_safe`).

- [ ] **Step 1: Create `_checksums.py`**

Copy from `clarinet/utils/file_checksums.py`: `CHUNK_SIZE`, `_sha256`, `_sha256_safe`, `compute_file_checksum`, `checksums_changed` (verbatim). Do **not** copy `compute_checksums` (its per-record logic moves to `Files.checksums`). Repoint: `from clarinet.utils.fs import run_in_fs_thread` → `from clarinet.files._fs import run_in_fs_thread`.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_files_facade.py
import pytest

@pytest.mark.asyncio
async def test_checksum_missing_returns_none(tmp_path):
    from clarinet.files._checksums import compute_file_checksum, checksums_changed
    assert await compute_file_checksum(tmp_path / "nope.bin") is None
    assert checksums_changed({"a": "1"}, {"a": "2", "b": "9"}) == {"a", "b"}
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py::test_checksum_missing_returns_none -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/_checksums.py tests/test_files_facade.py
git commit -m "refactor(files): move checksum primitives to files/_checksums"
```

---

## Phase B — The `Files` facade

### Task 6: `Files` constructor + `dir` / `dirs` / `empty`

**Files:**
- Create: `clarinet/files/facade.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Consumes: `_resolver`, `_patterns.fields_from`, `_storage`, `_template`, `_checksums`, `_fs`.
- Produces: `clarinet.files.facade.Files` with `__init__(entity, *, parent=None, fallback=False)`, `dir(level=None)`, `dirs()`, classmethod `empty()`, and the private `_lookup`.

- [ ] **Step 1: Create `facade.py` with the constructor + dir/dirs/empty**

```python
# clarinet/files/facade.py
"""Single public facade for on-disk path resolution and file access."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel
from clarinet.settings import settings

from clarinet.files import _checksums, _fs, _patterns, _resolver, _storage, _template

if TYPE_CHECKING:
    from clarinet.config.primitives import FileDef
    from clarinet.models.file_schema import FileDefinitionRead
    from clarinet.models.patient import PatientRead
    from clarinet.models.record import RecordRead
    from clarinet.models.study import SeriesRead, StudyRead

    type FileDefArg = FileDefinitionRead | FileDef | str
    type Entity = RecordRead | SeriesRead | StudyRead | PatientRead

__all__ = ["Files"]


class Files:
    """The sole public entry point for path resolution and file access.

    Construct from any of ``RecordRead`` / ``SeriesRead`` / ``StudyRead`` /
    ``PatientRead``. Strict by default (``AnonPathError`` for not-yet-anonymized
    records when the template references ``{anon_*}``); pass ``fallback=True``
    for UX call sites. ``parent`` supplies fallback values for pattern fields
    (``{user_id}``, inverted ``{origin_type}``, …).
    """

    def __init__(
        self,
        entity: "Entity",
        *,
        parent: "RecordRead | None" = None,
        fallback: bool = False,
    ) -> None:
        from clarinet.models.patient import PatientRead
        from clarinet.models.record import RecordRead
        from clarinet.models.study import SeriesRead, StudyRead

        self._parent = parent
        self._accessed: dict[str, Path] = {}

        if isinstance(entity, RecordRead):
            self._dirs = _resolver.build_working_dirs(entity, fallback_to_unanonymized=fallback)
            self._level = DicomQueryLevel(entity.record_type.level)
            self._registry = {fd.name: fd for fd in (entity.record_type.file_registry or [])}
            self._fields = _patterns.fields_from(entity, parent)
        elif isinstance(entity, SeriesRead):
            self._dirs = _resolver.build_working_dirs_from_series(
                entity, fallback_to_unanonymized=fallback
            )
            self._level = DicomQueryLevel.SERIES
            self._registry = {}
            self._fields = {}
        elif isinstance(entity, StudyRead):
            self._dirs = _resolver.build_working_dirs_from_study(
                entity, fallback_to_unanonymized=fallback
            )
            self._level = DicomQueryLevel.STUDY
            self._registry = {}
            self._fields = {}
        elif isinstance(entity, PatientRead):
            self._dirs = _resolver.build_working_dirs_from_patient(
                entity, fallback_to_unanonymized=fallback
            )
            self._level = DicomQueryLevel.PATIENT
            self._registry = {}
            self._fields = {}
        else:
            raise TypeError(
                "Files accepts RecordRead/SeriesRead/StudyRead/PatientRead, "
                f"got {type(entity).__name__}"
            )

    @classmethod
    def empty(cls) -> "Files":
        """Degenerate resolver for ``build_task_context``'s no-entity branch."""
        self = cls.__new__(cls)
        self._dirs = {}
        self._level = DicomQueryLevel.SERIES
        self._registry = {}
        self._fields = {}
        self._parent = None
        self._accessed = {}
        return self

    # ── working dirs ──
    def dir(self, level: DicomQueryLevel | None = None) -> Path:
        return self._dirs[level or self._level]

    def dirs(self) -> dict[DicomQueryLevel, Path]:
        return dict(self._dirs)

    # ── internal ──
    def _lookup(self, file_def: "FileDefArg") -> Any:
        if isinstance(file_def, str):
            return self._registry[file_def]
        return file_def
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_files_facade.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock
from clarinet.models.base import DicomQueryLevel


def _record(monkeypatch, *, registry=None, level="series"):
    monkeypatch.setattr(
        "clarinet.files._resolver.settings",
        MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"),
    )
    r = MagicMock()
    r.clarinet_storage_path = None
    r.id = 7; r.user_id = "u1"; r.patient_id = "P1"
    r.patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    r.study = MagicMock(study_uid="S", anon_uid="S"); r.study_uid = "S"
    r.series = MagicMock(series_uid="SE", anon_uid="SE", modality="CT", series_number=1); r.series_uid = "SE"
    r.record_type = MagicMock(level=level, file_registry=registry or [])
    r.record_type.name = "seg"
    r.data = {}
    # make isinstance(r, RecordRead) true:
    from clarinet.models.record import RecordRead
    r.__class__ = RecordRead
    return r


def test_files_dir_and_dirs(monkeypatch):
    from clarinet.files.facade import Files
    f = Files(_record(monkeypatch))
    assert f.dir() == Path("/data/CLARINET_1/S/SE")
    assert f.dir(DicomQueryLevel.PATIENT) == Path("/data/CLARINET_1")
    assert set(f.dirs()) == {DicomQueryLevel.PATIENT, DicomQueryLevel.STUDY, DicomQueryLevel.SERIES}


def test_files_rejects_bad_type():
    from clarinet.files.facade import Files
    with pytest.raises(TypeError):
        Files(object())


def test_files_empty():
    from clarinet.files.facade import Files
    assert Files.empty().dirs() == {}
```

Note: `_record` swaps `__class__` so `isinstance` passes against the real `RecordRead`; if that proves brittle for a MagicMock, build a minimal real `RecordRead` instead (see `tests/test_pipeline_context.py::_make_record_read` for the established pattern and reuse it).

- [ ] **Step 3: Run them**

Run: `uv run pytest tests/test_files_facade.py -k "files_dir or files_rejects or files_empty" -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/facade.py tests/test_files_facade.py
git commit -m "feat(files): Files facade constructor + dir/dirs/empty"
```

### Task 7: `Files.resolve` / `exists` / `glob` / `accessed`

**Files:**
- Modify: `clarinet/files/facade.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Produces: `Files.resolve(file_def, **overrides) -> Path`, `Files.exists(file_def, **overrides) -> bool`, `Files.glob(file_def) -> list[Path]`, `Files.accessed` (property).

- [ ] **Step 1: Add the methods to `Files`**

```python
# clarinet/files/facade.py — inside class Files, after _lookup
    def resolve(self, file_def: "FileDefArg", **overrides: Any) -> Path:
        fd = self._lookup(file_def)
        working_dir = self._dirs[fd.level or self._level]
        filename = _template.render(
            fd.pattern, {**self._fields, **overrides}, mode=_template.RenderMode.LENIENT
        )
        path = working_dir / filename
        self._accessed.setdefault(fd.name, path)
        return path

    def exists(self, file_def: "FileDefArg", **overrides: Any) -> bool:
        return self.resolve(file_def, **overrides).is_file()

    def glob(self, file_def: "FileDefArg") -> list[Path]:
        fd = self._lookup(file_def)
        working_dir = self._dirs[fd.level or self._level]
        paths = _patterns.glob_file_paths(fd, working_dir)
        self._accessed.setdefault(fd.name, paths[0] if paths else working_dir)
        return paths

    @property
    def accessed(self) -> dict[str, Path]:
        return dict(self._accessed)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_files_facade.py
def test_files_resolve(monkeypatch):
    from clarinet.files.facade import Files
    fd = MagicMock(name="fd"); fd.name = "seg"; fd.pattern = "seg_{id}.nrrd"; fd.level = None
    f = Files(_record(monkeypatch, registry=[fd]))
    assert f.resolve("seg") == Path("/data/CLARINET_1/S/SE/seg_7.nrrd")
    assert f.accessed["seg"] == Path("/data/CLARINET_1/S/SE/seg_7.nrrd")
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py::test_files_resolve -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/facade.py tests/test_files_facade.py
git commit -m "feat(files): Files.resolve/exists/glob/accessed"
```

### Task 8: `Files.render` + `Files.render_template`

**Files:**
- Modify: `clarinet/files/facade.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Produces: `Files.render(pattern) -> str` (record-based, uses constructor `parent`); staticmethod `Files.render_template(pattern, fields, *, strict=False) -> str`.

- [ ] **Step 1: Add the methods**

```python
# clarinet/files/facade.py — instance method
    def render(self, pattern: str) -> str:
        return _template.render(pattern, self._fields, mode=_template.RenderMode.LENIENT)

# clarinet/files/facade.py — staticmethod
    @staticmethod
    def render_template(pattern: str, fields: dict[str, Any], *, strict: bool = False) -> str:
        mode = _template.RenderMode.STRICT if strict else _template.RenderMode.LENIENT
        return _template.render(pattern, fields, mode=mode)
```

- [ ] **Step 2: Write the failing test (behavior change #1)**

```python
# tests/test_files_facade.py
def test_files_render_uses_unified_engine(monkeypatch):
    from clarinet.files.facade import Files
    rec = _record(monkeypatch)
    rec.data = {"mods": ["SR", "CT"]}
    f = Files(rec)
    assert f.render("{data.mods}_{id}") == "CT_SR_7"  # type-aware list coercion

def test_files_render_template_strict_raises():
    import pytest
    from clarinet.files.facade import Files
    with pytest.raises(KeyError):
        Files.render_template("{missing}", {}, strict=True)
    assert Files.render_template("{missing}", {}) == ""
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py -k "render" -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/facade.py tests/test_files_facade.py
git commit -m "feat(files): Files.render + render_template over one engine"
```

### Task 9: `Files.checksums` + checksum classmethods (behavior #4–#5)

**Files:**
- Modify: `clarinet/files/facade.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Produces: `async Files.checksums(defs=None) -> dict[str, str]`; staticmethods `async Files.checksum(path) -> str | None`, `Files.checksums_changed(old, new) -> set[str]`.

- [ ] **Step 1: Add the methods**

```python
# clarinet/files/facade.py — instance method
    async def checksums(self, defs: "list[FileDefinitionRead] | None" = None) -> dict[str, str]:
        """SHA256 of registered files, keyed by name (singular) / ``name:filename``
        (collections). Resolves each def at its own ``level``; missing files are
        omitted. Replaces both ``snapshot_checksums`` and ``compute_checksums``."""
        targets = defs if defs is not None else list(self._registry.values())
        out: dict[str, str] = {}
        for fd in targets:
            working_dir = self._dirs.get(fd.level or self._level)
            if working_dir is None:
                continue
            if fd.multiple:
                for p in await _fs.run_in_fs_thread(_patterns.glob_file_paths, fd, working_dir):
                    c = await _checksums.compute_file_checksum(p)
                    if c is not None:
                        out[f"{fd.name}:{p.name}"] = c
            else:
                filename = _template.render(
                    fd.pattern, self._fields, mode=_template.RenderMode.LENIENT
                )
                c = await _checksums.compute_file_checksum(working_dir / filename)
                if c is not None:
                    out[fd.name] = c
        return out

# clarinet/files/facade.py — staticmethods
    @staticmethod
    async def checksum(path: Path) -> str | None:
        return await _checksums.compute_file_checksum(path)

    @staticmethod
    def checksums_changed(old: dict[str, str] | None, new: dict[str, str]) -> set[str]:
        return _checksums.checksums_changed(old, new)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_files_facade.py
@pytest.mark.asyncio
async def test_files_checksums_omits_missing(monkeypatch):
    from clarinet.files.facade import Files
    fd = MagicMock(); fd.name = "seg"; fd.pattern = "seg_{id}.nrrd"; fd.level = None; fd.multiple = False
    f = Files(_record(monkeypatch, registry=[fd]))
    assert await f.checksums() == {}  # file does not exist on disk → omitted
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py::test_files_checksums_omits_missing -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/facade.py tests/test_files_facade.py
git commit -m "feat(files): unified Files.checksums + checksum classmethods"
```

### Task 10: stateless dir classmethods — `for_reader` / `working_dirs`

**Files:**
- Modify: `clarinet/files/facade.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Produces: classmethod `Files.for_reader(record) -> Files`; classmethod `Files.working_dirs(*, patient, study, series, storage_path=None, template=None, fallback=False, anon_patient_id=None, anon_study_uid=None, anon_series_uid=None) -> dict[DicomQueryLevel, Path]`.

- [ ] **Step 1: Add the classmethods**

```python
# clarinet/files/facade.py
    @classmethod
    def for_reader(cls, record: "RecordRead") -> "Files":
        """Strict first; on ``AnonPathError`` rebuild with raw-UID fallback."""
        try:
            return cls(record)
        except AnonPathError:
            return cls(record, fallback=True)

    @classmethod
    def working_dirs(
        cls,
        *,
        patient: Any,
        study: Any,
        series: Any,
        storage_path: Path | None = None,
        template: str | None = None,
        fallback: bool = False,
        anon_patient_id: str | None = None,
        anon_study_uid: str | None = None,
        anon_series_uid: str | None = None,
    ) -> dict[DicomQueryLevel, Path]:
        """Stateless all-levels renderer from explicit entities (caller indexes by level)."""
        return _storage.render_all_levels(
            patient=patient,
            study=study,
            series=series,
            storage_path=storage_path or Path(settings.storage_path),
            template=template,
            fallback_to_unanonymized=fallback,
            anon_patient_id=anon_patient_id,
            anon_study_uid=anon_study_uid,
            anon_series_uid=anon_series_uid,
        )
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_files_facade.py
def test_files_working_dirs_classmethod(monkeypatch):
    from clarinet.files.facade import Files
    monkeypatch.setattr("clarinet.files.facade.settings", MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"))
    monkeypatch.setattr("clarinet.files._storage.settings", MagicMock(storage_path="/data", disk_path_template="{anon_patient_id}/{study_uid}/{series_uid}"))
    patient = MagicMock(id="P1", anon_id="CLARINET_1", auto_id=1)
    dirs = Files.working_dirs(patient=patient, study=None, series=None, template="{anon_patient_id}/{study_uid}/{series_uid}")
    assert dirs[DicomQueryLevel.PATIENT] == Path("/data/CLARINET_1")
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py::test_files_working_dirs_classmethod -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/facade.py tests/test_files_facade.py
git commit -m "feat(files): Files.for_reader + Files.working_dirs classmethods"
```

### Task 11: remaining classmethods — `origin_type` / `display_anon_id` / `validate_template` / `in_thread` / `shutdown_io`

**Files:**
- Modify: `clarinet/files/facade.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Produces: staticmethods `Files.origin_type(record, parent=None)`, `Files.display_anon_id(study_uid, study_anon_uid)`, `Files.validate_template(template)`, `async Files.in_thread(fn, *args)`, `Files.shutdown_io()`.

- [ ] **Step 1: Add the methods**

```python
# clarinet/files/facade.py
    @staticmethod
    def origin_type(record: "RecordRead", parent: "RecordRead | None" = None) -> str:
        return _patterns.resolve_origin_type(record, parent)

    @staticmethod
    def display_anon_id(study_uid: str | None, study_anon_uid: str | None) -> str | None:
        return _storage.compute_display_anon_id(study_uid, study_anon_uid)

    @staticmethod
    def validate_template(template: str) -> str:
        return _template.validate_template(template)

    @staticmethod
    async def in_thread(fn: Any, *args: Any) -> Any:
        return await _fs.run_in_fs_thread(fn, *args)

    @staticmethod
    def shutdown_io() -> None:
        _fs.shutdown_fs_executor()
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_files_facade.py
def test_files_misc_classmethods():
    from clarinet.files.facade import Files
    assert Files.validate_template("{patient_id}/{study_uid}/{series_uid}")
    child = MagicMock(); child.record_type = MagicMock(); child.record_type.name = "c"
    parent = MagicMock(); parent.record_type = MagicMock(); parent.record_type.name = "p"
    assert Files.origin_type(child, parent) == "p"
    assert Files.origin_type(child) == "c"

@pytest.mark.asyncio
async def test_files_in_thread():
    from clarinet.files.facade import Files
    assert await Files.in_thread(lambda x: x + 1, 41) == 42
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/test_files_facade.py -k "misc_classmethods or in_thread" -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add clarinet/files/facade.py tests/test_files_facade.py
git commit -m "feat(files): remaining Files classmethods (origin_type, anon id, template, io)"
```

### Task 12: lazy `__init__.py` (PEP 562)

**Files:**
- Modify: `clarinet/files/__init__.py`
- Test: `tests/test_files_facade.py`

**Interfaces:**
- Produces: `from clarinet.files import Files` and `from clarinet.files import AnonPathError`; importing `clarinet.files._template` pulls no models/services.

- [ ] **Step 1: Write the lazy init**

```python
# clarinet/files/__init__.py
"""Public facade for on-disk path resolution and file access.

Only ``Files`` (and ``AnonPathError`` for ``except`` clauses) are public.
Lazy ``__getattr__`` keeps this package import-light so the stdlib-only
``clarinet.files._template`` leaf stays importable from ``clarinet.settings``
without dragging in models / services (avoids a bootstrap import cycle).
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarinet.exceptions.domain import AnonPathError
    from clarinet.files.facade import Files

__all__ = ["Files", "AnonPathError"]


def __getattr__(name: str) -> object:
    if name == "Files":
        from clarinet.files.facade import Files

        return Files
    if name == "AnonPathError":
        from clarinet.exceptions.domain import AnonPathError

        return AnonPathError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_files_facade.py
def test_public_facade_import():
    from clarinet.files import Files, AnonPathError
    assert Files.__name__ == "Files"
    assert issubclass(AnonPathError, Exception)


def test_template_leaf_import_is_light():
    import subprocess, sys
    code = (
        "import clarinet.files._template;"
        "import sys;"
        "bad=[m for m in sys.modules if m.startswith(('clarinet.models','clarinet.services'))];"
        "assert not bad, bad"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
```

- [ ] **Step 3: Run them**

Run: `uv run pytest tests/test_files_facade.py -k "public_facade or leaf_import_is_light" -v`
Expected: PASS.

- [ ] **Step 4: `make check` on the new package**

Run: `uv run make check > /tmp/test-files-facade.txt 2>&1` (or `uv run ruff check clarinet/files && uv run mypy clarinet/files`)
Expected: clean (the new package is self-consistent; old modules still present).

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/__init__.py tests/test_files_facade.py
git commit -m "feat(files): lazy public facade __init__ (PEP 562)"
```

---

## Phase C — Migrate call-sites (clean break)

Each task rewrites one group of imports/usages from the old modules to `Files`, then runs that area's tests. The old modules still exist (deleted in Phase D), so each task is independently green.

### Task 13: `settings.py`

**Files:** Modify `clarinet/settings.py:177`

- [ ] **Step 1: Repoint the private-leaf import**

In the `validate_template` validator, change `from clarinet.utils.path_template import validate_template` → `from clarinet.files._template import validate_template`. (Keep it function-level — settings must not import the facade package eagerly.)

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "settings or disk_path_template" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add clarinet/settings.py && git commit -m "refactor(files): settings uses files._template leaf for validate_template"
```

### Task 14: `models/record.py`

**Files:** Modify `clarinet/models/record.py:407-411`

- [ ] **Step 1: Repoint `compute_display_anon_id`**

Change the function-level import `from ..services.common.storage_paths import compute_display_anon_id` → `from clarinet.files import Files`, and the call `compute_display_anon_id(...)` → `Files.display_anon_id(...)`.

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "record_read or display_anon" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add clarinet/models/record.py && git commit -m "refactor(files): record model uses Files.display_anon_id"
```

### Task 15: `record_service.py`

**Files:** Modify `clarinet/services/record_service.py` (imports + the call-sites at ~101, 768, 843, 889, 927-942, 1037, 1239-1252)

- [ ] **Step 1: Repoint imports and call-sites**

Replace imports:
- `from clarinet.repositories.file_repository import FileRepository`, `from clarinet.utils.file_checksums import checksums_changed, compute_checksums`, `from clarinet.utils.file_patterns import glob_file_paths, resolve_pattern`, `from clarinet.utils.fs import run_in_fs_thread` → `from clarinet.files import Files`.

Rewrite call-sites:
- `FileRepository.resolve_with_fallback(record_read)` returning `(working_dirs, default_dir)` → `f = Files.for_reader(record_read)`; then `f.dirs()` / `f.dir()`.
- `compute_checksums(output_defs, record_read, working_dir, parent=parent_read)` → `await Files(record_read, parent=parent_read).checksums(output_defs)`.
- `checksums_changed(old, new)` → `Files.checksums_changed(old, new)`.
- `resolve_pattern(fd.pattern, record, parent)` → `Files(record, parent=parent).render(fd.pattern)`.
- `glob_file_paths(file_def, target_dir)` (inside `run_in_fs_thread`) → keep glob via `Files(record_read, parent=parent_read).glob(file_def)` where a `Files` is in scope, else `await Files.in_thread(glob_file_paths, ...)` is replaced by building a `Files` and calling `.glob`. For the bare `run_in_fs_thread(p.unlink)` / `run_in_fs_thread(file_path.is_file)` calls → `Files.in_thread(p.unlink)` / `Files.in_thread(file_path.is_file)`.

(Read the file and apply per occurrence; the helper `_missing_files`/checksum helpers at the top of the module also call `resolve_pattern` — convert them too.)

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "record_service or check_files or checksum" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add clarinet/services/record_service.py && git commit -m "refactor(files): record_service uses Files facade"
```

### Task 16: `file_validation.py`

**Files:** Modify `clarinet/services/file_validation.py` (imports + lines ~104, 167, 169)

- [ ] **Step 1: Repoint**

- imports `FileRepository`, `resolve_pattern`, `run_in_fs_thread` → `from clarinet.files import Files`.
- `FileRepository.resolve_with_fallback(record)` → `f = Files.for_reader(record)`; `working_dirs, directory = f.dirs(), f.dir()`.
- `resolve_pattern(file_def.pattern, record, parent)` → `Files(record, parent=parent).render(file_def.pattern)`.
- `run_in_fs_thread(validator.validate, ...)` → `Files.in_thread(validator.validate, ...)`.

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "file_validation or validate_files" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add clarinet/services/file_validation.py && git commit -m "refactor(files): file_validation uses Files facade"
```

### Task 17: `slicer/context.py` (removes the `origin_type` patch)

**Files:** Modify `clarinet/services/slicer/context.py` (imports + lines ~170, 198-206, and the `render_template` use at ~89)

- [ ] **Step 1: Repoint and simplify**

- imports `FileResolver`, `resolve_origin_type`, `render_template`/`RenderMode` → `from clarinet.files import Files`.
- Line ~170: `working_dirs = FileResolver.build_working_dirs(record, fallback_to_unanonymized=True)` → build the facade once: `f = Files(record, parent=parent, fallback=True)`; then `working_dirs = f.dirs()`.
- Lines ~198-206 (the `if file_registry:` block): delete the manual `fields = FileResolver.build_fields(record)` + `if parent: fields["origin_type"] = resolve_origin_type(...)` + `resolver = FileResolver(...)`; use the already-built `f` as the resolver (`f.resolve(fd)` / `f.exists(fd)`).
- Line ~89: `render_template(template, format_vars, mode=RenderMode.STRICT)` → `Files.render_template(template, format_vars, strict=True)`.

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "slicer" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add clarinet/services/slicer/context.py && git commit -m "refactor(files): slicer context uses Files, drops manual origin_type patch"
```

### Task 18: pipeline `context.py` + `sync_wrappers.py` (subsumes #387)

**Files:** Modify `clarinet/services/pipeline/context.py` (TaskContext.files type, `files_for`, `build_task_context` ~212-254), `clarinet/services/pipeline/sync_wrappers.py` (`files_for`)

- [ ] **Step 1: Rewrite `build_task_context`**

- imports: `from clarinet.services.common.file_resolver import FileResolver, resolve_pattern_from_dict` and `from clarinet.utils.file_patterns import resolve_origin_type` → `from clarinet.files import Files`.
- `TaskContext.files: FileResolver` → `files: Files`; docstring "Sync file path resolver" stays.
- `TaskContext.files_for(self, record)` body `return FileResolver.from_record(record)` → `return Files(record)`.
- `SyncTaskContext.files_for` (sync_wrappers.py): function-level `from clarinet.files import Files`; `return Files(record)`.
- `build_task_context`: replace the assembled-resolver logic with:
  - record_id branch: load `parent` when `record.parent_record_id` (as today, but unconditionally pass it): `files = Files(record, parent=parent)` — delete the manual `fields["origin_type"] = resolve_origin_type(...)` override (now centralized in `fields_from`). Keep the `try/except` around the parent fetch; on failure pass `parent=None`.
  - series branch: `files = Files(series)`.
  - study branch: `files = Files(study)`.
  - empty branch: `files = Files.empty()`.
  - Remove the trailing `files = FileResolver(working_dirs=..., ...)` block.
- Keep the `_resolve_pattern_from_dict = resolve_pattern_from_dict` back-compat alias only if a grep shows a remaining reference; otherwise delete it. Replace its definition with `from clarinet.files import Files` + `_resolve_pattern_from_dict = Files.render_template` **only if referenced**.

- [ ] **Step 2: Test**

Run: `uv run pytest tests/test_pipeline_context.py -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS (note: `TestFromRecord` is migrated in Task 22; if it fails here on `from_record`, that is expected and fixed there — run the rest with `-k "not FromRecord"` if needed).

- [ ] **Step 3: Commit**

```bash
git add clarinet/services/pipeline/context.py clarinet/services/pipeline/sync_wrappers.py
git commit -m "refactor(files): pipeline context builds Files; files_for returns Files"
```

### Task 19: anonymization + dicomweb writers/readers

**Files:** Modify `clarinet/services/anonymization_service.py` (~402-416), `clarinet/services/dicomweb/cache.py` (build_context/render_working_folder usage), `clarinet/services/pipeline/tasks/cache_dicomweb.py` (~86-95)

- [ ] **Step 1: Repoint each `build_context` + `render_working_folder` pair**

The uniform transform: `ctx = build_context(patient=…, study=…, series=…, template=T, anon_*=…)` followed by `render_working_folder(T, LEVEL, ctx, storage_path)` → `Files.working_dirs(patient=…, study=…, series=…, storage_path=storage_path, template=T, anon_patient_id=…, anon_study_uid=…, anon_series_uid=…)[LEVEL]`.

- `anonymization_service.py`: imports `from clarinet.services.common.storage_paths import build_context, render_working_folder` → `from clarinet.files import Files`; the ~402-416 block → `series_dir = Files.working_dirs(patient=patient, study=study, series=series, storage_path=Path(settings.storage_path), anon_patient_id=anon_patient_id, anon_study_uid=anon_study_uid, anon_series_uid=anon_series_uid)[DicomQueryLevel.SERIES]`.
- `dicomweb/cache.py`: same import swap; replace its `build_context`+`render_working_folder` call with the indexed `Files.working_dirs(...)[level]`.
- `pipeline/tasks/cache_dicomweb.py`: function-level `from clarinet.files import Files` (keep `AnonPathError` via `from clarinet.files import AnonPathError` if caught); replace the `build_context`+`render_working_folder` pair likewise.

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "anonymiz or dicomweb or cache_dicomweb" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add clarinet/services/anonymization_service.py clarinet/services/dicomweb/cache.py clarinet/services/pipeline/tasks/cache_dicomweb.py
git commit -m "refactor(files): anonymization + dicomweb use Files.working_dirs"
```

### Task 20: `cli/anon.py`

**Files:** Modify `clarinet/cli/anon.py` (~46-50 imports, ~223-228, ~410-411)

- [ ] **Step 1: Repoint**

- imports `from clarinet.services.common.storage_paths import (AnonPathError, build_context, render_working_folder, validate_template)` → `from clarinet.files import Files, AnonPathError`.
- migration dir rendering (~223-228): `ctx_from = build_context(...); old_dir = render_working_folder(from_template, level, ctx_from, storage_path)` → `old_dir = Files.working_dirs(patient=patient, study=study, series=series, storage_path=storage_path, template=from_template)[level]`; same for `new_dir` with `to_template`.
- `validate_template(args.from_template)` → `Files.validate_template(args.from_template)` (both lines).

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "anon_migrate or migrate_paths or cli_anon" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS (or no tests collected — then run `uv run clarinet anon migrate-paths --help` to confirm import health).

- [ ] **Step 3: Commit**

```bash
git add clarinet/cli/anon.py && git commit -m "refactor(files): cli anon uses Files.working_dirs + Files.validate_template"
```

### Task 21: `api/app.py` lifespan + pipeline checksum callers

**Files:** Modify `clarinet/api/app.py` (~48 import + lifespan shutdown), `clarinet/services/pipeline/task.py:148`, `clarinet/services/pipeline/tasks/convert_series.py:64`

- [ ] **Step 1: Repoint**

- `api/app.py`: `from clarinet.utils.fs import shutdown_fs_executor` → `from clarinet.files import Files`; the lifespan `shutdown_fs_executor()` call → `Files.shutdown_io()`.
- `pipeline/task.py` & `convert_series.py`: function-level `from clarinet.utils.file_checksums import compute_file_checksum` → `from clarinet.files import Files`; `compute_file_checksum(path)` → `Files.checksum(path)`.

- [ ] **Step 2: Test**

Run: `uv run pytest tests/ -k "lifespan or app_startup or convert_series or task_run" -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add clarinet/api/app.py clarinet/services/pipeline/task.py clarinet/services/pipeline/tasks/convert_series.py
git commit -m "refactor(files): app lifespan + pipeline checksum callers use Files"
```

### Task 22: migrate existing tests (incl. #387 `TestFromRecord`)

**Files:** Modify every test importing a deleted module — find with the grep below; notably `tests/test_pipeline_context.py` (`TestFromRecord`), plus any `tests/` importing `file_resolver` / `storage_paths` / `file_repository` / `path_template` / `anon_resolve` / `file_patterns` / `file_checksums` / `utils.fs`.

- [ ] **Step 1: Enumerate**

```bash
grep -rlE "file_resolver|storage_paths|file_repository|path_template|anon_resolve|file_patterns|file_checksums|from clarinet.utils.fs" tests/
```

- [ ] **Step 2: Rewrite each per the migration map**

For `tests/test_pipeline_context.py::TestFromRecord`: `FileResolver.from_record(record)` → `Files(record)`; `ctx.files_for(other)` assertions keep working (now returns `Files`); `resolver.dir()` / `.resolve("seg")` are `Files` methods. For others, apply the § "Call-site migration map" from the spec.

- [ ] **Step 3: Run the migrated tests**

Run: `uv run pytest tests/test_pipeline_context.py tests/test_files_facade.py tests/test_files_fields_from.py -v > /tmp/test-files-facade.txt 2>&1`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/ && git commit -m "test(files): migrate tests to Files facade"
```

---

## Phase D — Finalize

### Task 23: delete old modules + full pipeline

**Files:** Delete the 8 old modules; check `services/common/`.

- [ ] **Step 1: Confirm no remaining references**

```bash
grep -rnE "from clarinet.services.common.file_resolver|from clarinet.services.common.storage_paths|from clarinet.repositories.file_repository|from clarinet.utils.path_template|from clarinet.utils.anon_resolve|from clarinet.utils.file_patterns|from clarinet.utils.file_checksums|from clarinet.utils.fs|import FileResolver|import FileRepository" clarinet/ tests/
```
Expected: **no output** (every reference migrated). Fix any stragglers before deleting.

- [ ] **Step 2: Delete**

```bash
git rm clarinet/repositories/file_repository.py \
       clarinet/services/common/file_resolver.py \
       clarinet/services/common/storage_paths.py \
       clarinet/utils/path_template.py \
       clarinet/utils/anon_resolve.py \
       clarinet/utils/file_patterns.py \
       clarinet/utils/file_checksums.py \
       clarinet/utils/fs.py
```
Then update `clarinet/repositories/__init__.py` (drop the `FileRepository` re-export) and `clarinet/services/common/__init__.py` (drop the `FileResolver` / `resolve_pattern_from_dict` re-exports). If `services/common/` now holds only `__init__.py`, check for any `import clarinet.services.common` users (`grep -rn "services.common" clarinet/ tests/`); if none beyond the package itself, `git rm clarinet/services/common/__init__.py` and remove the dir.

- [ ] **Step 3: `make check`**

Run: `uv run make check > /tmp/test-files-facade.txt 2>&1`
Expected: format + lint + typecheck clean.

- [ ] **Step 4: Full pipeline**

Run: `timeout 2400 make test-all-stages > /tmp/test-files-facade.txt 2>&1` (`SKIP_VM=1` acceptable per local env; keep schema + PG + e2e)
Expected: all stages green.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "refactor(files): delete legacy path/file modules (clean break)"
```

### Task 24: downstream migration prompt for `clarinet_nir_liver`

**Files:** Create `docs/superpowers/specs/2026-06-19-files-facade-downstream-migration-prompt.md`

- [ ] **Step 1: Write the self-contained prompt**

Author a prompt the user pastes into a Claude session **inside `clarinet_nir_liver`** (after this facade has merged + the project bumps its clarinet version). It must:
- State the goal: migrate off `FileResolver` / `render_all_levels` to `clarinet.files.Files`.
- Pattern A (`plan/workflows/tasks.py` ×3 + `scripts/repair_missing_nifti.py` ×1): replace the 5-line `FileResolver(build_working_dirs(X), level, registry, build_fields(X))` idiom with `ctx.files_for(X)` (in tasks with `ctx`) or `Files(X)` (in the standalone script); update imports `from clarinet.services.pipeline.context import FileResolver` / `from clarinet.services.common.file_resolver import FileResolver` → `from clarinet.files import Files`.
- Pattern B (`plan/hydrators/context_hydrators.py`): `render_all_levels(patient=…, study=…, series=…, storage_path=…, fallback_to_unanonymized=True)` → `Files.working_dirs(patient=…, study=…, series=…, storage_path=…, fallback=True)`; import `from clarinet.services.common.storage_paths import render_all_levels` → `from clarinet.files import Files`.
- Pattern C (`ctx.files.resolve/.exists`, 30+ sites): **no change**.
- Verification: run the project's test suite + `python -c "import plan.workflows.tasks, plan.hydrators.context_hydrators"`.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-19-files-facade-downstream-migration-prompt.md
git commit -m "docs(files): downstream clarinet_nir_liver migration prompt"
```

---

## Self-Review

**1. Spec coverage:**
- Package layout + lazy init → Tasks 1, 12. ✓
- `Files` full surface (dir/dirs/resolve/exists/glob/render/checksums/accessed + classmethods for_reader/empty/working_dirs/render_template/origin_type/display_anon_id/validate_template/checksum/checksums_changed/in_thread/shutdown_io) → Tasks 6-11. ✓
- Unified engine (`_template.render` + `fields_from`) → Task 4, 8. ✓
- Behavior changes #1 (list coercion) → Task 8; #2 (parent fallback) → Tasks 4/18; #3 (slicer patch removed) → Task 17; #4–#5 (checksum contract) → Task 9. ✓
- All call-site migrations → Tasks 13-21; tests → Task 22. ✓
- Downstream (#387 subsumed, patterns A/B/C) → Tasks 18, 24. ✓
- Delete old + full pipeline → Task 23. ✓

**2. Placeholder scan:** No "TBD"/"TODO"/"add error handling". Move tasks give exact source paths + exact import edits; novel code is inlined complete.

**3. Type consistency:** `fields_from(record, parent=None)` (Task 4) consumed by `Files.__init__` and `Files.render` (Tasks 6, 8). `Files.working_dirs(...)` classmethod (Task 10) used by Tasks 19/20 and the downstream prompt (Task 24). `Files.for_reader` (Task 10) used by Tasks 15/16. `_resolver.build_working_dirs*` (Task 3) consumed by `Files.__init__` (Task 6). Names consistent across tasks.
