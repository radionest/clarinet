# Downstream migration prompt — `clarinet_nir_liver` → `clarinet.files.Files`

This file is a **ready-to-paste prompt**. After the clarinet `files-facade` refactor has
merged and `clarinet_nir_liver` has bumped its pinned clarinet version, open a Claude
session **inside the `clarinet_nir_liver` repo** and paste everything in the box below.

The clarinet refactor removed the old path/file machinery (`FileResolver`,
`FileRepository`, `clarinet.services.common.storage_paths`, `clarinet.utils.path_template`,
etc.) and replaced it with a single public facade, `clarinet.files.Files`. The
`ctx.files.resolve(...)`/`.exists(...)` surface is unchanged; only direct `FileResolver` /
`render_all_levels` usages need migrating.

---

```
Migrate this project off the removed clarinet path/file APIs onto the new
`clarinet.files.Files` facade. The clarinet dependency was upgraded; `FileResolver`,
`FileRepository`, and `clarinet.services.common.storage_paths` no longer exist. Only the
direct usages below need changing — the 30+ `ctx.files.resolve(...)` / `ctx.files.exists(...)`
call sites need NO change (`ctx.files` is now a `Files` instance with the same methods).

Make exactly these changes, then verify.

## Pattern A — replace the hand-assembled `FileResolver(...)` idiom (cross-record resolution)

There are 4 sites that build a resolver for a *parent* / *reloaded* / *other* record `X`:
- `plan/workflows/tasks.py` (3 sites, ~lines 485, 690, 1175) — inside pipeline tasks that
  have `ctx`.
- `scripts/repair_missing_nifti.py` (1 site, ~line 32) — a standalone script with no `ctx`.

Each looks like:
```python
r = FileResolver(
    working_dirs=FileResolver.build_working_dirs(X),
    record_type_level=X.record_type.level,
    file_registry=X.record_type.file_registry or [],
    fields=FileResolver.build_fields(X),
)
r.resolve(some_file_def)
```

Replace each with a single call:
- In the 3 `tasks.py` sites (where `ctx` is in scope): `ctx.files_for(X).resolve(some_file_def)`
  (`ctx.files_for(record)` returns a `Files` for any record you already hold).
- In `scripts/repair_missing_nifti.py` (no `ctx`): `Files(X).resolve(some_file_def)`.

Update the imports accordingly:
- `from clarinet.services.pipeline.context import FileResolver`  → remove (use `ctx.files_for`); if the file
  still needs `Files` directly, `from clarinet.files import Files`.
- `from clarinet.services.common.file_resolver import FileResolver` (in `repair_missing_nifti.py`)
  → `from clarinet.files import Files`.

The resolved paths are byte-identical: `Files(X)` performs exactly the old 4-argument
construction (`build_working_dirs` + level + registry + `build_fields`), and `Files`'s
field building is behavior-equivalent to the old `build_fields` (it additionally applies
type-aware list coercion — a list-valued `{data.FIELD}` now renders as `"CT_SR"` rather
than `"['CT', 'SR']"`).

## Pattern B — replace `render_all_levels(...)` (cross-record directory rendering)

`plan/hydrators/context_hydrators.py` (~line 60, in `_target_working_dirs`) calls:
```python
from clarinet.services.common.storage_paths import render_all_levels
...
return render_all_levels(
    patient=record.patient, study=study, series=series,
    storage_path=storage_path, fallback_to_unanonymized=True,
)
```
Replace with the facade classmethod (note `fallback_to_unanonymized=` becomes `fallback=`):
```python
from clarinet.files import Files
...
return Files.working_dirs(
    patient=record.patient, study=study, series=series,
    storage_path=storage_path, fallback=True,
)
```
`Files.working_dirs(...)` returns the same `dict[DicomQueryLevel, Path]`; the existing
`dirs[DicomQueryLevel.STUDY]` / `[DicomQueryLevel.SERIES]` indexing stays.

## Pattern C — `ctx.files.resolve(...)` / `ctx.files.exists(...)` (30+ sites)

**No change.** `ctx.files` is now a `Files` instance and keeps `.resolve`/`.exists`/`.glob`/`.dir`.

## Facade reference (only these are needed here)
- `Files(record)` / `ctx.files_for(record)` → entity-bound resolver; `.resolve(fd)`, `.exists(fd)`, `.glob(fd)`, `.dir(level=None)`, `.dirs()`.
- `Files(record, fallback=True)` → UX/raw-UID fallback (no `AnonPathError` on not-yet-anon records).
- `Files.working_dirs(*, patient, study, series, storage_path=None, template=None, fallback=False, anon_*=None)` → stateless all-levels `dict[level, Path]`.
- The machinery is private behind `clarinet/files/_*` — do NOT import those leaves.

## Verify
1. `grep -rnE "FileResolver|FileRepository|storage_paths|render_all_levels|build_working_dirs|build_fields|from clarinet.services.common" plan/ scripts/` → only `ctx.files_for` / `Files` references remain (no `FileResolver`/`render_all_levels`).
2. `python -c "import plan.workflows.tasks, plan.hydrators.context_hydrators"` → imports cleanly.
3. Run the project's test suite (`make test` / `pytest`) — the file-resolution paths (pattern A/B) and the unchanged `ctx.files` paths (pattern C) must pass.
4. If any pattern-A site renders a file pattern that references a list-valued `{data.FIELD}`, confirm the new `"CT_SR"`-style join is the intended filename (it is the corrected behavior).
```

---

## Notes for the clarinet maintainer (not part of the pasted prompt)

- The downstream change lands as its own PR in `clarinet_nir_liver`, after the clarinet
  facade PR merges and the project bumps its clarinet pin.
- Pattern C (30+ `ctx.files.resolve`) needs no edits because the refactor deliberately
  preserved the `ctx.files` method surface and kept `ctx.files` parent-less.
- If the project later wants the parent-fallback for `{user_id}`-style placeholders to flow
  through `ctx.files` itself (instead of the separate pattern-A resolver), that is a
  follow-up — the facade supports `ctx.files_for(record)` for explicit cross-record work.
