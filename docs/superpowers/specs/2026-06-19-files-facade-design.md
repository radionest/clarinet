# Design: `Files` — single facade for path resolution & file access

**Date:** 2026-06-19
**Status:** Approved design (pre-implementation)
**Branch:** `worktree-files-facade`

## Context

File access and on-disk path resolution are currently spread across **10 modules in
3 packages** (~1850 LOC). The scoped `clarinet/CLAUDE.md` declares `FileRepository`
the "sole authority for resolving on-disk paths", but that authority leaks: a second
near-homonym class (`FileResolver`) is imported and used directly by `slicer/context`
and `pipeline/context`, and the two coexist with several lower-level helpers. The
result is an abstraction leak and semantic ambiguity — there is no single front door,
and two different `{placeholder}` engines can render the *same* pattern *differently*.

This document specifies a refactor to a single public facade with all machinery made
private, including a genuine unification of the two pattern engines.

## Problem — the concrete leaks

1. **Two homonym classes, overlapping role.** `FileResolver` (`services/common/`) and
   `FileRepository` (`repositories/`). `FileRepository` claims sole authority but
   `FileResolver` is used directly in `slicer/context.py` and `pipeline/context.py`,
   bypassing it. The monopoly is fictional.

2. **`FileRepository` is mis-filed as a repository.** It lives in `repositories/` but
   has no `AsyncSession`, doesn't extend `BaseRepository`, raises no domain
   `*NotFoundError` — it is a stateless path facade. It also inverts the layer graph
   (`repositories/` importing from `services/`).

3. **Two `{placeholder}` engines with divergent semantics:**
   - `resolve_pattern_from_dict` → `render_template` (`utils/path_template.py`):
     dict-based, type-aware coercion (a list field → `"CT_SR"`), regex
     `\{[a-zA-Z_][\w.]*\}`, dotted-path walk through nested mappings.
   - `resolve_pattern` (`utils/file_patterns.py`): record-object-based, fallback
     chain + inverted `origin_type` virtual field, regex `\{[^}]+\}`, `str()` coercion
     (a list field → `"['CT', 'SR']"`).

   The same pattern for the same file renders differently depending on which code path
   touches it (`.resolve()` for the write path vs `resolve_pattern` for the
   checksum/validation path). **Evidence the split is a latent bug, not a feature:**
   `slicer/context.py` manually patches `fields["origin_type"] = resolve_origin_type(...)`
   *after* calling `build_fields`, because `build_fields` produces the wrong (non-inverted)
   `origin_type`.

4. **No front door.** Machinery is scattered across `utils/` (path_template,
   anon_resolve, file_patterns, file_checksums, fs), `services/common/`
   (file_resolver, storage_paths) and `repositories/` (file_repository). New code has
   nowhere obvious to import from.

5. **Duplicated dispatch.** `FileResolver` exposes four `build_working_dirs_from_*`
   static factories; `FileRepository.__init__` re-dispatches the same four entity types
   by `isinstance`.

## Locked decisions

| Decision | Choice | Rationale |
|---|---|---|
| **Depth** | C — facade + relabel + **unify pattern engines** | The dual engine is the core semantic ambiguity. |
| **Scope** | Groups 1+2 — path resolution **+** file I/O (checksums, fs) | "доступ к файлам И резолв пути". Group 3 (registry config loading, DB link sync) is a different responsibility and stays out. |
| **Form** | Single public class **`Files`** in `clarinet/files/`, machinery behind `_`-private modules, lazy `__init__` (PEP 562) | One public name, maximally minimal import surface. |
| **Naming** | `Files` | Short, idiomatic; package/class redundancy is an accepted common pattern. |
| **Migration** | **Clean break** — rewrite all ~50 call-sites, delete old modules, **no aliases** | A lingering `FileResolver`/`FileRepository` alias would re-introduce the two-name ambiguity the refactor removes. |

## Goals

- Exactly one public entry point (`Files`) for resolving working directories, resolving
  file definitions to paths, rendering patterns, computing checksums, and the file-I/O
  thread pool.
- All other machinery private (`_`-prefixed leaf modules inside `clarinet/files/`).
- A single rendering engine and a single field-source builder, so any given pattern
  resolves identically on every code path.
- No behavior change beyond the explicitly enumerated deltas (§ Behavior changes).

## Non-goals

- Touching `file_registry_resolver.py` (loads `file_registry.toml` at bootstrap) or
  `file_link_sync.py` (writes M2M link rows) — these are registration/DB concerns,
  not path resolution. They stay in `utils/`.
- Changing the `disk_path_template` setting, its placeholder catalogue, or the
  anonymization contract.
- Changing the public HTTP API or any DB schema.

## Relationship to PR #387

PR #387 — `feat(pipeline): add FileResolver.from_record and ctx.files_for` (**merged**) —
is the baseline: it added exactly the record→resolver ergonomics this facade delivers
natively, so its symbols already exist in `main` and the migration treats them as existing
code. Account for it as follows:

- **`FileResolver.from_record(record)`** — 1-arg classmethod collapsing the 4-arg
  constructor (`build_working_dirs` + level + `file_registry` + `build_fields`).
  **Subsumed:** the `Files(record)` constructor *is* `from_record`. Clean-break migrate
  `FileResolver.from_record(r)` → `Files(r)`.
- **`ctx.files_for(record)` / `SyncTaskContext.files_for(record)`** — pipeline sugar over
  `from_record` to resolve a record you already hold (parent / reloaded / cross-patient);
  "also works in standalone scripts that have no `ctx`" via `from_record`. **Preserved as
  a stable API:** both keep the `files_for` method, now returning a `Files` instance
  (identical `.resolve/.exists/.glob/.dir` surface). This is the migration target for the
  downstream `tasks.py` pattern-A sites.

The facade goes **beyond** #387: #387 deliberately leaves the verbose constructor in place
for the slicer `origin_type`-override call site and `build_task_context`'s series/study
branches. The facade removes the slicer special case (inverted `origin_type` centralized
in `fields_from` → `Files(record, parent=parent)`), and the series/study branches become
`Files(series)` / `Files(study)` (working-dir-only access — no file registry, matching
#387's own note); the empty branch is unchanged.

**Baseline.** #387 is already merged, so the facade builds directly on it: `ctx.files_for`
survives unchanged (stable API, new return type), `from_record` folds into `Files(...)`,
and #387's `TestFromRecord` tests (`tests/test_pipeline_context.py`) migrate to the facade.
The implementation worktree (`worktree-files-facade`) was branched before #387 landed —
**rebase it onto the current `origin/main` (with #387) before implementation** so the
migration sees `from_record`/`files_for` in the baseline.

## Target package layout

```
clarinet/files/
  __init__.py     # PUBLIC: exposes only `Files` (+ re-export AnonPathError). Lazy __getattr__ (PEP 562).
  facade.py       # `Files` — the single public class
  _template.py    # ← utils/path_template.py            stdlib-only renderer (the ONE engine) + validate_template
  _storage.py     # ← services/common/storage_paths.py  DB-aware level/dir rendering, anon-id derivation
  _anon.py        # ← utils/anon_resolve.py             require_anon_or_raw
  _patterns.py    # ← utils/file_patterns.py            fields_from (record→dict), glob_file_paths, virtual fields
  _resolver.py    # ← services/common/file_resolver.py  working_dirs build + file-def resolution core
  _checksums.py   # ← utils/file_checksums.py
  _fs.py          # ← utils/fs.py                        async FS thread pool
```

**Deleted after migration:** `repositories/file_repository.py`,
`services/common/file_resolver.py`, `services/common/storage_paths.py`,
`utils/path_template.py`, `utils/anon_resolve.py`, `utils/file_patterns.py`,
`utils/file_checksums.py`, `utils/fs.py`. If `services/common/` is left with only
`__init__.py` and no other importers, delete the package; otherwise keep it.

**Kept (out of scope):** `utils/file_registry_resolver.py`, `utils/file_link_sync.py`.

## Public API — `Files`

Constructed from any of `RecordRead` / `SeriesRead` / `StudyRead` / `PatientRead`.
The constructor performs the single entity-type dispatch (removing the duplicated
dispatch of leak #5), builds all available working directories once, and builds the
canonical field mapping once (see § Unified engine).

```python
class Files:
    def __init__(self, entity, *, parent=None, fallback=False) -> None: ...
```

- `parent` — optional fallback record for pattern fields (`{user_id}`, `{origin_type}`, …).
- `fallback` — `False` (default, strict: `AnonPathError` when an entity is not yet
  anonymized and the template references `{anon_*}`); `True` for UX call sites
  (raw-UID fallback).

### Instance methods (entity-bound)

| Method | Replaces | Returns |
|---|---|---|
| `.dir(level=None)` | `FileRepository.working_dir` (property) **and** `FileResolver.dir(level)` | `Path` at `level` (default: entity's level) |
| `.dirs()` | `FileRepository.working_dirs_all()` | `dict[DicomQueryLevel, Path]` |
| `.resolve(file_def, **overrides)` | `FileRepository.resolve_file` / `FileResolver.resolve` | `Path` |
| `.exists(file_def, **overrides)` | `FileResolver.exists` | `bool` |
| `.glob(file_def)` | `FileResolver.glob` | `list[Path]` |
| `.render(pattern)` | `resolve_pattern(pattern, record, parent)` | `str` (uses constructor's `parent`) |
| `await .checksums(defs=None)` | `FileResolver.snapshot_checksums` **and** `compute_checksums` | `dict[str, str]` (missing omitted) |
| `.accessed` (property) | `FileResolver.accessed_files` | `dict[str, Path]` |

`.dir(...)`, `.resolve(...)`, `.exists(...)`, `.glob(...)` preserve the method names
that pipeline task code reaches through `ctx.files`, so `plan/` custom code is
unaffected (see § Migration).

**`.checksums(defs=None)` reconciles two divergent helpers.** Today
`FileResolver.snapshot_checksums` (full registry, singular-only, keyed by name,
includes `None` for missing) and `compute_checksums` (caller-supplied subset, globs
collections as `name:filename`, omits missing, record-level dir for *all* defs) compute
checksums differently. The unified method: iterates `defs` (default — full registry),
resolves each def **at its own `level`** (consistent with `.resolve`), globs collections
(`name:filename`) and keys singular by name, and **omits** missing files. See behavior
changes #4–#5.

### Classmethods (stateless)

| Classmethod | Replaces | Notes |
|---|---|---|
| `Files.for_reader(record)` | `FileRepository.resolve_with_fallback` | Returns a `Files` (strict first; on `AnonPathError` re-builds with `fallback=True`). Callers read `.dirs()` / `.dir()`. |
| `Files.empty()` | the empty-context `FileResolver(working_dirs={}, …, fields={})` | Degenerate resolver (empty dirs/registry/fields, SERIES level) for `build_task_context`'s no-entity branch. `.resolve` raises `KeyError`; only present so `ctx.files` stays non-`None`. |
| `Files.working_dirs(*, patient, study, series, storage_path=None, template=None, fallback=False, anon_patient_id=None, anon_study_uid=None, anon_series_uid=None)` | `render_all_levels` **and** (`build_context` + `render_working_folder`) | Stateless all-levels renderer from **explicit** entities (not a single record); caller indexes by level. Covers: anon writer (`[SERIES]` with `anon_*` overrides), dicomweb cache, cli `anon migrate-paths` (per-call `template=`), pipeline `cache_dicomweb`, **and the downstream `clarinet_nir_liver` hydrator** (§ Downstream impact). The instance `.dirs()` delegates here. |
| `Files.render_template(pattern, fields, *, strict=False)` | `render_template` / `resolve_pattern_from_dict` | Dict-based render over the ONE engine. STRICT used by slicer user-arg rendering. |
| `Files.origin_type(record, parent=None)` | `resolve_origin_type` | |
| `Files.display_anon_id(study_uid, study_anon_uid)` | `compute_display_anon_id` | Used by `models/record.py` (function-level import). |
| `Files.validate_template(template)` | `validate_template` | Public entry; `settings.py` keeps the private-leaf import (see § Layering). |
| `await Files.checksum(path)` | `compute_file_checksum` | |
| `Files.checksums_changed(old, new)` | `checksums_changed` | Pure. |
| `await Files.in_thread(fn, *args)` | `run_in_fs_thread` | Generic FS thread pool runner. |
| `Files.shutdown_io()` | `shutdown_fs_executor` | Called from the app lifespan. |

`derive_anon_patient_id` has no external callers → fully private in `_storage.py`.

## Unified rendering engine (the depth-C change)

The two engines conflate two **independent** concerns: *where field values come from*
(record + fallbacks + virtual fields vs a raw dict) and *how a value is coerced to a
string* (`str()` vs type-aware). The refactor separates them into **one renderer** and
**one field-source builder**.

### One renderer — `_template.render`

The existing `render_template` (type-aware `coerce_field_value`, dotted-path walk,
`STRICT`/`LENIENT` modes) becomes the **only** substitution algorithm. Its bespoke
counterpart inside `resolve_pattern` (the `str()`-coercion loop) is **deleted**.

```python
def render(template, mapping, *, mode=RenderMode.LENIENT,
           list_separator="_", list_sorted=True,
           missing="", on_missing_leave_as_is=False) -> str: ...
```

### One field-source builder — `_patterns.fields_from`

```python
def fields_from(record, parent=None) -> dict[str, Any]: ...
```

Produces the canonical field dict consumed by `render`. Unifies the three current
field sources (`FileResolver.build_fields`, `resolve_record_field`, and the slicer
manual `origin_type` patch):

- Scalar placeholders (`id`, `user_id`, `patient_id`, `study_uid`, `series_uid`):
  primary record value, else `parent`'s value when the primary is missing/empty.
- `record_type` → `{"name": record.record_type.name}`.
- `data` → merged `{**(parent.data or {}), **(record.data or {})}` for `{data.FIELD}`
  dotted access (record wins per key; parent fills absent keys).
- `origin_type` → **inverted** priority: `parent.record_type.name` first, else
  `record.record_type.name` (the virtual-field rule, now centralized — slicer's manual
  patch is removed).

When `parent=None`, `fields_from(record)` is equivalent to today's `build_fields(record)`
except for the type-aware coercion fix.

### Routing

- `Files(entity, parent=...).resolve(fd)` → `render(fd.pattern, fields_from(entity, parent) | overrides, LENIENT)` against the file-def's working dir.
- `Files(entity, parent=...).render(pattern)` → `render(pattern, fields_from(entity, parent), LENIENT)`.
- `Files.render_template(pattern, dict, strict=)` → `render(pattern, dict, STRICT|LENIENT)` directly.

Two honestly-named entry points (record-based `.render` / `.resolve`, dict-based
`.render_template`) over **one** engine → identical coercion everywhere.

## Behavior changes (explicit)

1. **List coercion on the record path.** A list-valued `{data.FIELD}` rendered through
   the record path (`.resolve` / `.render` / checksums) now yields the sorted,
   `_`-joined string (`"CT_SR"`) instead of the Python list repr (`"['CT', 'SR']"`).
   This is the intended fix; no test locks the old repr (the only repr-referencing
   test, `tests/test_dicom_operations.py`, asserts the *already-fixed* DICOM-modalities
   path). A new unit test pins the corrected behavior.

2. **Parent fallback applies to all scalar placeholders on every path.** Previously the
   write path (`FileResolver.resolve` via `build_fields`) applied no parent fallback,
   while the checksum/validation path (`resolve_pattern`) did. Unifying on `fields_from`
   means `.resolve` now honors `parent` for `{user_id}` etc., aligning the write path
   with the checksum/validation path and removing the latent resolve-vs-checksum
   filename divergence. Each call-site passes the same `parent` it passes today, so the
   only paths that change are those that were already inconsistent.

3. **Slicer `origin_type` patch removed.** `slicer/context.py` no longer manually
   overrides `origin_type`; it constructs `Files(record, parent=parent, fallback=True)`
   and the inverted `origin_type` (plus parent scalar fallback) comes from `fields_from`.

4. **Checksum snapshot contract.** The pre-task snapshot path (was
   `snapshot_checksums`) now globs collection (`multiple=True`) file definitions and
   omits missing files, instead of singular-only with `None` placeholders. Change
   detection (`checksums_changed`) treats an absent key as "not present", so the
   pipeline pre/post comparison is unaffected — verified by test.

5. **Cross-level checksum dirs.** The change-detection scan (was `compute_checksums`)
   now resolves each file definition at its own `level` (matching `.resolve`), instead
   of computing every def against the single record-level directory. This only differs
   for file definitions whose `level` ≠ the record's level; it corrects a latent
   resolve-vs-checksum directory mismatch.

6. **Data-dict merge replaces per-key falsy fallback for `{data.FIELD}`.** The old
   `resolve_pattern` treated `{data.FIELD}` like a scalar — a falsy `record.data[FIELD]`
   (including a present-but-empty `""`) fell back to `parent.data[FIELD]`. `fields_from`
   instead merges `{**(parent.data or {}), **(record.data or {})}`, so a present-but-empty
   `record.data[FIELD]` now wins its key and does **not** fall back; only keys *absent*
   from `record.data` are filled from the parent. This aligns `{data.FIELD}` with the
   record-wins semantics used everywhere else `data` is read and diverges from the old
   validation/checksum path only when a record carries an explicit empty value for a key
   the parent fills — which no repo pattern relies on.

No other behavior changes are intended. Regex strictness is safe: every real file/path
pattern in the repo (`{id}`, `{patient_id}`, `{study_uid}`, `{series_anon_uid}`,
`{study_anon_uid}`, `{user_id}`, `{data.FIELD}`) satisfies the stricter
`\{[a-zA-Z_][\w.]*\}`.

## Layering & import-cycle constraints

- **Lazy `__init__` (PEP 562).** `clarinet/files/__init__.py` must expose `Files` (and
  `AnonPathError`) via module-level `__getattr__`, with **no heavyweight top-level
  imports**. This keeps the stdlib-only `_template.py` leaf importable without dragging
  in models/services.
- **`settings.py`** keeps a function-level `from clarinet.files._template import
  validate_template` (the private leaf). Importing the facade package top-level from
  settings would cycle (`settings → files → models → settings`). Documented bootstrap
  exception; `Files.validate_template` is the public entry for everyone else.
- **`models/record.py`** uses a function-level `from clarinet.files import Files` for
  `Files.display_anon_id` — mirrors today's function-level import of
  `compute_display_anon_id`, avoiding an init-time cycle.
- **`_resolver.py` / `_storage.py`** import models only under `TYPE_CHECKING` or at
  function level, as the current modules do.

## Call-site migration map (~50 files)

| From | To |
|---|---|
| `from clarinet.repositories.file_repository import FileRepository`; `FileRepository(x)` | `from clarinet.files import Files`; `Files(x)` |
| `FileRepository(x).working_dir` | `Files(x).dir()` |
| `repo.resolve_file(fd)` | `Files(x).resolve(fd)` |
| `FileRepository.resolve_with_fallback(rec)` → `(dirs, dir)` | `f = Files.for_reader(rec)` → `f.dirs(), f.dir()` |
| `FileResolver.build_working_dirs(rec, fallback_to_unanonymized=True)` + `FileResolver(...)` | `Files(rec, fallback=True[, parent=...])` |
| `FileResolver.from_record(rec)` (PR #387) | `Files(rec)` |
| `ctx.files_for(rec)` / `SyncTaskContext.files_for(rec)` (PR #387) | **kept** — method returns `Files(rec)` instead of `FileResolver` |
| `from clarinet.utils.file_patterns import resolve_pattern`; `resolve_pattern(p, rec, parent)` | `Files(rec, parent=parent).render(p)` |
| `resolve_origin_type(rec, parent)` | `Files.origin_type(rec, parent)` |
| `build_context(...)` + `render_working_folder(...)` (anon writer, dicomweb cache, cli anon, pipeline cache_dicomweb) | `Files.working_dirs(...)[level]` |
| `from clarinet.utils.fs import run_in_fs_thread`; `run_in_fs_thread(fn, *a)` | `Files.in_thread(fn, *a)` |
| `shutdown_fs_executor()` (api/app lifespan) | `Files.shutdown_io()` |
| `compute_file_checksum(p)` / `compute_checksums(defs, rec, dir, parent)` / `checksums_changed(...)` | `Files.checksum(p)` / `Files(rec, parent=parent).checksums(defs)` / `Files.checksums_changed(...)` |
| `compute_display_anon_id(...)` (models/record.py) | `Files.display_anon_id(...)` |
| `from clarinet.utils.path_template import validate_template` (settings.py) | `from clarinet.files._template import validate_template` (private leaf, unchanged pattern) |
| `pipeline/context.py` `build_task_context` (`ctx.files` was `FileResolver`) | record_id branch → `Files(record, parent=parent)` (parent loaded as today; manual `fields['origin_type']` override **deleted** — `fields_from` centralizes it). series/study branches → `Files(series)` / `Files(study)`. empty branch → `Files.empty()`. `.resolve/.exists/.glob/.dir` names preserved; keep `_resolve_pattern_from_dict` shim if still referenced. |

## Downstream impact — `clarinet_nir_liver`

`clarinet_nir_liver` (separate repo at `../clarinet_nir_liver`) consumes this machinery
directly, so the clean break requires a coordinated companion change there. Three usage
patterns:

**A. Direct `FileResolver(X)` cross-record resolution** — `plan/workflows/tasks.py` (×3,
including one function-level import) and `scripts/repair_missing_nifti.py` (×1). Every
site is the identical idiom, building a resolver for a *parent* / *sibling* / *fresh*
record `X` to resolve a file against `X`'s identity:

```python
r = FileResolver(
    working_dirs=FileResolver.build_working_dirs(X),
    record_type_level=X.record_type.level,
    file_registry=X.record_type.file_registry or [],
    fields=FileResolver.build_fields(X),
)
r.resolve(fd)
```

→ collapses to `Files(X).resolve(fd)` — **byte-identical path** (the `Files` constructor
performs exactly this construction; with no `parent=`, `fields_from(X)` equals today's
`build_fields(X)`). A strict ergonomic win (5 lines → 1). Where `ctx` is in scope (the
three `tasks.py` sites), the idiomatic target is `ctx.files_for(X).resolve(fd)` (the
PR #387 helper, preserved); the standalone `repair_missing_nifti.py` script (no `ctx`)
uses `Files(X).resolve(fd)`.

**B. `render_all_levels(...)` cross-record dir rendering** —
`plan/hydrators/context_hydrators.py` renders STUDY/SERIES dirs for an explicit
`(patient, study, series)` triple (not derived from one record), with
`fallback_to_unanonymized=True`. → `Files.working_dirs(patient=…, study=…, series=…,
storage_path=…, fallback=True)`, then index `[DicomQueryLevel.STUDY/SERIES]`.

**C. `ctx.files.resolve(...)` / `.exists(...)`** — 30+ sites in `tasks.py`. **No call-site
change** (`.resolve`/`.exists`/`.glob`/`.dir` preserved). `ctx.files` becomes
`Files(record, parent=parent)`: `build_task_context` already loads the parent to override
`origin_type` (today a manual `fields["origin_type"] = resolve_origin_type(...)` patch); the
facade folds that into `fields_from(record, parent)`, which additionally makes the other
scalars fall back to parent consistently (behavior change #2). The 30+ sites resolve
`master_model` / projection / resection patterns (no `{user_id}`), so their paths are
unchanged; the `{user_id}` segmentation patterns flow through the *separate* pattern-A
resolver, also unchanged.

No other clarinet symbols are imported downstream (no `render_template`, `resolve_pattern`,
`build_context`, etc.).

**Coordination.** clarinet and clarinet_nir_liver are separate repos; this session cannot
open a cross-repo PR. Deliverable instead: a **self-contained migration prompt** (committed
beside this spec) for the user to run from a Claude session inside `clarinet_nir_liver`
once the facade has merged. It rewrites the 4 pattern-A sites + 1 pattern-B site and their
two import statements; the 30+ pattern-C (`ctx.files_for`) sites need no edit. Downstream
pins a clarinet version, so it keeps building until it bumps.

## Testing strategy

- Migrate existing tests that import the deleted modules to the `Files` facade —
  including PR #387's `TestFromRecord` in `tests/test_pipeline_context.py`
  (`from_record` → `Files(rec)`; `files_for` assertions retargeted at the `Files`
  return type).
- New unit tests:
  - List-valued `{data.FIELD}` renders as `"CT_SR"` on the record path (behavior change #1).
  - `Files` rejects an unsupported entity type with `TypeError`.
  - `Files.for_reader` falls back on `AnonPathError`; strict `Files(record)` raises.
  - `fields_from` parent-fallback for scalars and inverted `origin_type`.
  - `.checksums(defs)` globs collections (`name:filename`), omits missing, and resolves
    cross-level defs at their own level (behavior changes #4–#5); a pipeline pre/post
    `checksums_changed` round-trip is unaffected.
  - Lazy `__init__`: importing `clarinet.files._template` does not import
    `clarinet.models` / `clarinet.services` (assert via `sys.modules`).
- Run the full `make test-all-stages` pipeline — the refactor touches the record,
  pipeline, slicer, anonymization, and dicomweb paths.
- After the companion downstream change, run `clarinet_nir_liver`'s test suite and an
  import smoke-check (`python -c "import plan.workflows.tasks, plan.hydrators.context_hydrators"`)
  against the new clarinet to confirm pattern A/B migrations and the unchanged `ctx.files`
  surface (pattern C).

## Out of scope / follow-ups

- `file_registry_resolver.py` and `file_link_sync.py` remain in `utils/` (Group 3).
- Potential later move of the FS thread pool out of `Files` if more non-file callers
  appear (today every caller is file-related).
