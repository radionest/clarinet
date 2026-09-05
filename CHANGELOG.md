# Changelog

## Unreleased

### Added

- **`dicom_scp_enabled` names which process owns the C-MOVE listener.** A
  listening port belongs to one process and the PACS routes C-MOVE by
  destination AET to a host and port it was configured with, so on a c-move
  deployment every retrieving process needs its own registered `(AET, port)`.
  `None` (the default) owns a listener in the API when the retrieve mode is a
  c-move mode; `false` never does; `true` always does. A worker takes one only
  when asked — `--dicom` or `dicom_scp_enabled=true` — since the API already
  holds `dicom_aet:dicom_port` on such a deployment.
  `clarinet worker --dicom AET:PORT` now implies `true` alongside the AET, port
  and mode it already set, so the flag still works where one shared
  `EnvironmentFile` says otherwise, and warns when it overrides an explicit
  `false`. A bind collision in the API lifespan is now a `StartupError` naming
  the port, the AET and the ways out (the worker still surfaces the same message
  as an `OSError`). A worker configured for a c-move mode but holding no
  listener now warns at startup, naming `--dicom AET:PORT`, instead of starting
  cleanly and failing at its first retrieve.

### Breaking

- **The DICOM core moved to the `dimsechord` package.** `clarinet.services.dicom`
  no longer exports `DicomOperations`, `StorageHandler`, `StorageMode`,
  `StorageConfig`, `AssociationConfig`, `RetrieveRequest` or
  `QueryRetrieveLevel.PATIENT` — they described the inline SCU/SCP, now deleted.
  The generic Q/R models (`DicomNode`, `StudyQuery`/`SeriesQuery`/`ImageQuery`,
  `StudyResult`/`SeriesResult`/`ImageResult`, `RetrieveResult`,
  `BatchStoreResult`, `QueryRetrieveLevel`) are still importable from
  `clarinet.services.dicom` and `...dicom.models`, but they are now dimsechord
  **dataclasses**, not Pydantic models: construction is keyword-only and
  `.model_dump()` / `.model_validate()` are gone (use `dataclasses.asdict`).
  `DicomClient` keeps its full method surface and gains `dicom_retrieve_mode`
  dispatch, so `get_study` / `get_series` / `get_*_to_memory` run C-MOVE-to-self
  under a c-move mode instead of falling back to C-GET. Two wire-level changes
  come with the upgrade: Q/R now uses the **Study Root** information model
  exclusively (Patient-Root-only peers will refuse the context), and the C-GET
  path — which `dicom_retrieve_mode` still selects by default — negotiates
  dimsechord's 26 curated storage classes with compressed transfer syntaxes
  rather than 120 classes uncompressed-only: a peer sending compressed objects
  now works, while a SOP class outside the curated set (X-Ray Angiographic,
  Nuclear Medicine, Digital Mammography, Enhanced XA, Breast Tomosynthesis and
  the VL family among them) comes back as a short series rather than an
  error. The C-MOVE path keeps the wider coverage: the Storage SCP accepts
  pynetdicom's 120 `StoragePresentationContexts` with every transfer syntax,
  because an acceptor matches the requester's proposals instead of proposing
  its own, so the 128-context budget never binds.
- **`RecordType.unique_by` replaces `unique_per_user`.** `unique_by:
  frozenset[str] | None` (subset of `{"user", "parent"}`) replaces the boolean
  `unique_per_user`: at most one record of the type may exist per unique
  combination of the selected scopes, within the type's own DICOM-level
  context; `None` disables the constraint (TOML spelling of off is `false` —
  TOML has no null); an empty set is rejected (use `None`, or `max_records=1`
  for one-per-level). Default is now `{"user", "parent"}`, versus the old
  implicit `unique_per_user=True` (`{"user"}` only) — a type that also sets
  `parent_record_id` now additionally partitions by parent, so distinct
  parents may each hold their own record where previously only one existed
  for the whole type+user. The deprecated `unique_per_user=True/False` kwarg
  still works on `RecordDef` and in TOML/API payloads (translates to
  `{"user"}`/`None`, emits `DeprecationWarning`; an explicit `unique_by` in
  the same payload wins). Bound-tuple rule: a `"user"`-selecting type's check
  is skipped while the candidate record is unassigned (pools stay creatable)
  and closes at claim/assign time; a `{"parent"}`-only type has no such gap
  and dedupes at creation. New `{parent_id}` file-pattern placeholder (the FK
  column, load-independent) lets an OUTPUT pattern discriminate by parent
  instance — unlike `{origin_type}`, which names only the parent's *type*.
- **Output-path uniqueness is validated fail-fast.** Every non-collection
  OUTPUT `FileRef` that hasn't opted out via `allow_path_collision=True` must
  embed the placeholder needed to keep coexisting records from overwriting
  each other's file: `{user_id}` for a `"user"` partition, `{parent_id}` for
  a `"parent"` partition, the RecordType's own level-UID placeholder when the
  file's own level is coarser than the RecordType's, and `{id}` when
  `unique_by=None` allows 2+ coexisting records with nothing else to
  distinguish them. Violations raise `RecordConstraintViolationError` at
  RecordType config load (Python/TOML) and at RecordType `POST`/`PATCH` — the
  PATCH guard re-validates the *merged* effective state, so a patch that
  breaks its own OUTPUT patterns is rejected immediately rather than failing
  at the next startup.
- **`parent_record_id` is now `ON DELETE CASCADE` (was `SET NULL`).** Deleting
  a record now cascades to its descendants at the DB level instead of
  orphaning them with a null parent. The framework's own cascade-delete flow
  (`RecordRepository.delete_records`, behind `DELETE /api/admin/records/{id}`)
  already pre-collects the full subtree before deleting and emits one
  `RecordEvent`/SSE `deleted` per collected id, so that path is unaffected.
  Accepted trade: a record removed purely as a side effect of the DB-level
  CASCADE — any delete path that doesn't pre-collect descendants — emits no
  `RecordEvent` for the cascaded rows.
- **SQLite now enforces foreign keys.** `PRAGMA foreign_keys=ON` is set on
  every file-based SQLite connection (`:memory:` test pools are unaffected).
  `ON DELETE CASCADE`/`SET NULL` FKs that were previously metadata-only on
  SQLite now actually fire, and a write that used to silently leave a
  dangling reference now fails outright. At startup, `DatabaseManager` runs
  `PRAGMA foreign_key_check` and logs a `WARNING` per violation found in a
  pre-existing database — diagnostic only, it never aborts startup.
- **Downstream migration.** Generate an Alembic migration adding
  `recordtype.unique_by` (nullable JSON) and
  `recordtypefilelink.allow_path_collision` (bool, `server_default=false`),
  and changing the `record.parent_record_id` FK to `ON DELETE CASCADE`. Do
  not let a plain `server_default`-driven backfill populate `unique_by` — it
  mis-backfills every existing row. Backfill via a `CASE` on the old
  `unique_per_user` column instead: `true → '["user"]'`, `false`/`NULL` →
  `NULL`. This is not optional: any existing `shared_editing=True` row
  necessarily has `unique_per_user=False` (an already-enforced invariant),
  and the new column's own `server_default` is `["parent", "user"]` —
  backfilling with the server_default instead of the `CASE` would give those
  rows a `unique_by` containing `"user"`, immediately violating
  "`shared_editing` requires `'user' not in unique_by`". The backfilled
  value is durable only for types whose config pins `unique_by` (or still
  passes the legacy `unique_per_user` kwarg/key): on first startup the
  reconciler self-heals any type whose config leaves `unique_by` unset
  toward the new default `["parent", "user"]`, overwriting the backfilled
  `["user"]` — pin `unique_by` explicitly for every type that must keep
  the legacy per-user semantics. Before relying on
  the new SQLite FK enforcement, audit for pre-existing dangling
  `parent_record_id` rows (the startup audit only warns, it doesn't fix),
  and expect legacy duplicate rows that violate the new default partition to
  need resolution — `clarinet_nir_liver`'s migration drops 25 pre-existing
  duplicate `review` records for exactly this reason.
- **`plan/` files now import via the `clarinet_plan.` prefix (single root).**
  At startup an in-memory anchor package `clarinet_plan` is rooted at the one
  `config_tasks_path`; every plan file is a submodule of it. Sibling-by-stem
  imports (`from record_types import ...`, `from utils.x import y`,
  `from tasks import ...`) no longer resolve — use
  `from clarinet_plan.record_types import ...` /
  `from clarinet_plan.utils.x import y` (or a relative `from .x import y`). A
  leftover un-prefixed import fails at startup with a migration hint naming the
  correct spelling. No directory is ever placed on `sys.path`.
- **`recordflow_paths` must live inside `config_tasks_path`.** A flow directory
  outside the single root raises `ConfigLoadError` at startup.
- **File and directory names on import paths must be valid Python identifiers.**
  `2_phase_flow.py`, `my-utils/`, or a keyword segment fail at startup with a
  message naming the file/dir to rename.
- **A `X.py` + `X/` name collision under the root is rejected** (Python would
  silently import only one) — rename or remove one.
- **Hydrator-file default names changed**: `config_schema_hydrators_file`
  `hydrators.py` → `schema_hydrators.py`; `config_context_hydrators_file`
  `context_hydrators.py` → `slicer_hydrators.py`. The setting names are
  unchanged — projects that set them explicitly are unaffected; projects on the
  defaults must rename the files (or pin the old names in `settings.toml`).
- **Ops**: the `call:` node-id in pipeline payloads now uses the
  `clarinet_plan.`-rooted module name. On upgrade, drain pipeline queues and
  restart the API and all workers together so both sides agree on the id format.
- **Slicer segmentation geometry guards.**
  `SlicerHelper.load_segmentation` now raises `SlicerHelperError` when a loaded
  `.seg.nrrd`'s reference geometry does not match the active source volume (when
  one is set), instead of silently re-gridding the mask onto the volume (which
  masked the projection Z-flip class of bug). The `SlicerHelper` set-operations
  (`subtract_segmentations` — both operands — / `merge_as_pool` /
  `binarize_and_split_islands`) now classify an empty labelmap export: a
  genuinely empty source is tolerated (warning + no-op / empty result),
  while a source that *carries* voxels but exports empty — a flipped/foreign grid
  that does not overlap the reference extent — raises pointing at
  `conform_seg_to_grid`. **Downstream migration:** projects with historically
  foreign-grid segmentations must conform them to their volume grid
  (`conform_seg_to_grid`) **before** upgrading — otherwise interactive Slicer
  scripts that `load_segmentation` a misaligned mask start raising. The
  empty-source set-op change is non-breaking (strictly more tolerant than the
  previous opaque `arrayFromVolume` crash).
- **Slicer set-ops fail fast on grid mismatch, not just empty export (#415).**
  `subtract_segmentations` / `binarize_and_split_islands` / `merge_as_pool` now raise
  `SlicerHelperError` when a non-empty input segmentation's reference geometry differs
  from the source volume grid (a partially-overlapping foreign grid that previously
  slipped past the empty-export guard). Pass `resample=True` to opt back into the
  legacy re-grid behavior. Genuinely-empty sources are still tolerated.
- **RecordType config drift now self-heals on reconcile (#389).** The config
  reconciler now heals any config-unset field that has a concrete (non-None)
  model default toward that default on restart — previously such a field, once
  drifted in the DB (migration backfill, manual SQL, a past `model !=
  server_default` mismatch), never reconciled. This covers every boolean flag
  (`unique_per_user`, `editable`, `mask_patient_data`, `parent_required`,
  `inherit_user_from_parent`, `shared_editing`) plus `min_records`, `level`, and
  `viewer_mode`; fields whose default is `None` (`max_records`,
  `edit_window_days`, `role_name`, …) keep the "unset = leave the DB row
  untouched" contract. Every heal is logged.
  **Downstream migration** — for a config-managed type that leaves a flag unset
  and whose DB value drifted from that flag's default:
  - `unique_per_user` also had its `server_default` aligned `false()`→`true()` to
    match the model default; it heals to `True`, and if the type already holds
    multiple records per user, new record creation then returns 409
    `UNIQUE_PER_USER`.
  - `editable` heals to `True`, which **re-opens** finished records to
    non-superusers (weakens the submit-time lock) wherever the DB had drifted to
    `False`.
  - `mask_patient_data` heals to `True` — strictly more masking (fail-safe).
  Set the affected flag explicitly in that type's config to keep the old value.
- **`SlicerHelper.subtract_segmentations` runs the shared correspondence
  engine.** Removal verdicts follow the identical `correspond()` →
  `Difference()` → `KeepPlan` path as `Segmentation.difference`, with operands
  labeled per segment on the Slicer side (vs autolabel components server-side),
  with one shared parameter set: new keyword-only `strategy=` (override built
  from bundle symbols) and `granularity="label"|"union"` (per-segment default vs
  the legacy sum-over-union flattening). The method now **requires the
  correspondence bundle** — it raises `SlicerHelperError` regardless of operand
  content unless the script ran with `include_correspondence=True`; record
  open/validate and the submit-path validator now always include the bundle, and
  ad-hoc `/exec` opts in via the new `SlicerExecRequest.include_correspondence`
  field. Verdicts shift in three cases vs the legacy voxel loop: fragmented
  sub-threshold overlap is now kept by default (`granularity="union"` restores
  removal), the ratio boundary tightened from `>` to `>=`, and with both scalars
  set the ratio wins (`max_overlap` is ignored). The scalars→strategy derivation
  is shared as `strategy_from_thresholds` in
  `clarinet.services.image.correspondence` and ships inside the bundle.
- **`export_segmentation`'s `reference_volume=` parameter is removed;
  `conform_to=` is the only export guard.** The old parameter compared two
  in-memory Slicer objects that Slicer's own load-time canonicalization had
  already flipped identically, so it could not detect the mirror it existed to
  catch (see the new `docs/grid-workflows.md`). `conform_to=<path to the
  reference volume file>` reads the reference's **on-disk** grid instead and
  classifies the segmentation node's current grid against it: `SAME` exports
  as-is, `REARRANGED` re-grids exactly onto the reference before exporting
  (layer/label-preserving for every layer representation — shared, separate,
  or mixed; caller's node untouched), `FOREIGN` raises without writing; the
  written file is then re-read and re-classified, deleting it on any
  post-write mismatch, including a strict per-segment check: any source
  segment with voxels that has no voxeled counterpart in the written file
  (matched by name) also deletes the file and raises, naming the lost
  segment(s).
  `assert_segmentation_matches_volume` is now private
  (`_assert_segmentation_matches_volume`) and remains only as
  `load_segmentation`'s best-effort load-time check. **Downstream migration:**
  replace `export_segmentation(name, path, reference_volume=<node>)` with
  `export_segmentation(name, path, conform_to=<volume file path>)` — a
  `TypeError` on upgrade names every call site that needs it.
- **`conform_seg_to_grid` raises on a `FOREIGN` grid pair by default.** It
  previously resampled unconditionally, including onto an unrelated study's
  grid. It now classifies the pair first (`SAME` no-op, `REARRANGED` exact
  index rearrangement, `FOREIGN` raises `GeometryMismatchError`) and only
  resamples a foreign pair when the caller passes `allow_resample=True`. Also
  gained a 4-D `(L, X, Y, Z)` layered `.seg.nrrd` repair path (preserves every
  segment's name/label value/layer verbatim), alongside the existing 3-D path.
  **Downstream migration:** a script relying on the old unconditional resample
  for genuinely unrelated grids must add `allow_resample=True`; a same-study
  pair that was always `SAME`/`REARRANGED` needs no change.
- **`Image.read`/`Image.read_nrrd` raise `ImageReadError` on a 4-D NRRD, and on
  a 3-D NRRD whose `space directions` are present without a supported `space`
  field.** A 4-D `.seg.nrrd` previously built a silently-wrong NaN-valued grid
  instead of raising — read it via `LayeredSegmentation` or `grid_io.read_grid`
  instead. A NRRD with `space directions` but no (or an unrecognized) `space`
  was previously treated as LPS regardless, silently misreading third-party
  RAS/LAS files; `space` is now honored (LPS as-is, RAS/LAS converted, anything
  else raises). **Downstream migration:** a clarinet-written NRRD from before
  2026-03-08 that carries `space directions` without a `space` field now fails
  to read — see `clarinet/docs/migration-orientation-0.10.17.md` for the
  one-time re-save fix.
- **DICOM→NIfTI conversion changes on-disk grid layout for every
  newly-converted volume (grid epoch).** The in-plane axis order now follows
  `ImageOrientationPatient` end-to-end (array, spacing, and direction move
  together — the internal row/column swap is gone), and the canonical slice
  sense is now the side of the IOP normal (`det = normal · slice > 0`,
  universal for non-degenerate series) instead of a fixed +dominant-axis
  convention. The change is always an exact, physically-equivalent index
  rearrangement (`REARRANGED`, zero voxel drift — never `FOREIGN`, never a
  mirror), but every legacy segmentation now sits on a different index grid
  than a freshly re-converted volume of the same series. **Blast radius:** any
  project comparing a pre-epoch segmentation against a re-converted volume by
  voxel index. **Remediation:** re-convert affected volumes, then conform
  legacy segmentations once against the new grid (`conform_seg_to_grid`,
  idempotent, exact for `REARRANGED` pairs) — see the conversion-orientation
  epoch section in `clarinet/docs/migration-orientation-0.10.17.md`.
  `coco_to_segmentation` was updated in lockstep (mask transposed onto the
  internal (x, y) axes; the NIfTI-convention flip moved to the width axis), so
  its physical output for a given COCO file + reference volume is unchanged
  across the epoch.
- **File patterns may no longer interpolate record data.** A
  `FileDefinition.pattern` containing `{data.FIELD}` (or bare `{data}`) is now
  rejected when the configuration loads. Migration: replace it with `{id}`,
  `{parent_id}` or `{user_id}`, or declare one `FileDefinition` per variant. If
  files already exist on disk under the old name, rename them to match the new
  pattern — the resolved filename changes with the pattern. This restriction is
  temporary and tracked by #552. **This rejection aborts startup** — the
  process does not come up — unlike the stored-row case below, which the
  running app tolerates by skipping just that one definition.
- **A file pattern must still render to a valid name when an optional
  placeholder is absent.** `{parent_id}`, `{user_id}`, `{study_uid}` and
  `{series_uid}` are legitimately empty for a parentless, unassigned,
  patient-level or study-level record, so a pattern that leans on one of them
  for a whole path segment degenerates: `{parent_id}.txt` → `.txt`,
  `{user_id}` → `""`, `{study_uid}/mask.nrrd` → `/mask.nrrd`, `{parent_id}.` →
  `.`. All four are now rejected when the configuration loads. Migration: give
  the affected segment some literal text — `report_{parent_id}.txt`,
  `seg_{user_id}.nrrd`, `study_{study_uid}/mask.nrrd`. Patterns resting on
  `{id}`, `{patient_id}`, `{record_type.name}` or `{origin_type}` need no
  change; those are never absent. **Collections (`multiple=True`) are exempt** —
  their placeholders are replaced with `*` and globbed rather than rendered, so
  `{parent_id}.nrrd` globs to `*.nrrd` and stays legal. `Files.resolve` now
  refuses a collection outright (`ValueError`) instead of rendering one, and
  the consumers that walk a whole registry skip collections before reaching it
  — see Fixed. `FileValidator.validate` no longer renders one either: a
  required collection INPUT is reported missing without rendering it (the
  verdict it already reached by rendering a wildcard to nothing), so the
  exemption is enforced by every in-tree consumer; matching collections by
  glob is issue #562. A *stored*
  row that violates the rule is
  skipped from `RecordType.file_registry` with a WARNING rather than being
  fatal (see Security below).
- **A file pattern may only use placeholders the renderer knows.** Every
  `{name}` in a `FileDefinition.pattern` must be one of `{id}`, `{parent_id}`,
  `{user_id}`, `{patient_id}`, `{study_uid}`, `{series_uid}`, `{origin_type}`
  or `{record_type.name}`; any other *name-shaped* placeholder is rejected when
  the configuration loads. Brace groups the renderer never substitutes are
  unaffected and keep rendering literally — `set{1}.nrrd` and
  `seg_{studyuid:s}.nrrd` stay legal, because `{1}` and a format spec are not
  placeholders as far as the renderer is concerned. Previously an unrecognised name silently substituted `""`, so a typo
  like `{studyuid}` either failed later at the working-directory join — a 500
  on every read of that record, including the Slicer open endpoint — or, in
  `{studyuid}.nrrd`, quietly resolved to the hidden file `.nrrd` and kept
  working against the wrong path. Migration: fix the spelling. **Collections
  (`multiple=True`) are exempt**, as they are from the rule above: a collection
  globs rather than renders and substitutes `*` for every placeholder whatever
  it is named, so `slice_{n}.dcm` → `slice_*.dcm` remains a legal
  positional-wildcard idiom.
- **Setting `Record.clarinet_storage_path` on record creation is now
  admin-only.** A non-admin supplying a non-`None` value is rejected (403) at
  the `POST /api/records` route — the only client-facing path that accepts the
  field. Creating a record without the field is unaffected. Both admitted
  caller kinds are covered: a superuser, and a non-superuser holding the
  built-in `admin` role.
- **Finishing a record now returns 422 when an OUTPUT file pattern cannot be
  safely resolved.** `POST /api/records/{id}/data` and
  `POST /api/records/{id}/submit` reject the submission with 422 when a
  `role=OUTPUT` `FileDefinition.pattern` would render to a path outside the
  record's working directory. The check runs before the submission is
  persisted, so a rejected submit leaves the record's data and status
  untouched (re-matched INPUT file links may already have committed); a backstop
  check during the post-submit checksum scan can also reject, after the
  record's own data has committed. Neither PATCH variant is affected — they
  update data without re-running the output-path checks. The detail names the
  offending file definition and the reason; it does **not** echo the value
  that tripped the guard, which with `{data.*}` banned can only be a stored
  identity field the caller may not be entitled to see.
- **A record whose `patient_id` is exactly `.` or `..` can no longer finish.**
  Both are legal under `PATIENT_ID_REGEX` (`^[A-Za-z0-9._\-^]{1,64}$`) but
  render to a bare directory reference, so a submit against a pattern
  interpolating `{patient_id}` now returns 422 where it previously succeeded
  with a logged warning. No known deployment holds such a patient id; rename
  it if one does.
- **Grid-conformance declarations on `FileDefinition` (`grid_conform_to` /
  `on_grid_mismatch`).** A file may declare that its on-disk voxel grid must
  match another file bound to the same RecordType, checked with the same
  `grid_relation` three-way taxonomy (`SAME`/`REARRANGED`/`FOREIGN`) as the
  rest of this changelog's grid work. Config load (Python/TOML, and
  RecordType `POST`/`PATCH`) rejects an unresolvable declaration: a
  reference not bound to the same RecordType, a reference whose effective
  DICOM level is finer than the declaring file's, the declaring file's
  effective DICOM level finer than the RecordType's own, `multiple=True` on
  either side, a self-reference, or a pattern `read_grid` cannot classify. At
  runtime an INPUT mismatch is never repaired or deleted, since a record
  does not own its inputs — it blocks the record at creation or the
  `preparing`→`pending` exit (check-files only withholds an existing
  block's auto-unblock, never causes one), or raises a 422 if a
  submission's own re-validation catches it first; an OUTPUT mismatch is
  enforced pre-commit, on all four submission endpoints —
  `POST`/`PATCH /records/{id}/submit` and
  `POST`/`PATCH /records/{id}/data` (the latter two because `POST /data`
  defaults to `status=finished` and is functionally a submission) — per
  `on_grid_mismatch`: `reject` (the default) 409s without touching the
  file, `conform` repairs an exactly-repairable `REARRANGED` pair via
  `conform_seg_to_grid` when the subject is already uint8 on disk (else
  409s untouched — the repair forces a uint8 cast and would otherwise
  silently quantize a wider format), still 409s a `FOREIGN` one, `delete`
  removes the file and 409s either verdict. **`delete` is irreversible
  and is armed even on a metadata-only `PATCH /data`** — an accepted
  hazard, not a bug; see the adoption order in `docs/grid-workflows.md`.
  **Downstream migration:** generate an Alembic revision adding two
  nullable columns, `filedefinition.grid_conform_to` and
  `filedefinition.on_grid_mismatch` (both `str`, no backfill needed) — the
  framework ships no migrations of its own.
- Both grid-mismatch rejections carry a machine-readable `code`:
  `GRID_MISMATCH` on the submit-time INPUT 422 (`InputGridMismatchError`, a
  `ValidationError` — a plain missing-input 422 stays code-less) and on
  every OUTPUT-guard 409 (`OutputGridMismatchError`, a
  `BusinessRuleViolationError`), so a client branches on the code and lets
  the status say which side. The INPUT 422 is logged at `WARNING` without a
  traceback, like the 409s, instead of `ERROR` with one.
- `POST /records/{id}/validate-files` previews the OUTPUT side of the guard
  read-only: every declared OUTPUT grid pair present on disk is run through
  the same `decide()` table, and each pair a submission would reject or
  delete is reported as a `grid_mismatch` error naming the action and the
  reason, so a client learns about a coming 409 before it submits; a pair
  `conform` would repair passes, and nothing is repaired or deleted.
  `check-files` stays INPUT-only by design — its verdict drives the
  `blocked` auto-unblock.

### Security

- Rendered file paths are now confined to the record's working directory. A
  substituted value containing `/`, `\`, or NUL is rejected, and a value that
  is exactly `.` or `..` is rejected separately; the joined path is then
  checked for containment (plus a NUL-byte rejection, below). The join is
  otherwise containment *only* — it accepts
  a dot-leading basename, because a hidden file is not an escape and
  rejecting it would hard-fail the legitimate absent-placeholder renders
  described under Breaking. Closes #521.
- A `RecordFileLink.filename` row persisted with a traversal before this release
  is now refused at read time instead of being followed. No migration is needed —
  the guard is the remediation.
- A legacy `FileDefinition.pattern` that fails the path-safety validator is now
  skipped from `RecordType.file_registry` (logged as a WARNING, once per
  process per record-type/definition/pattern rather than once per request —
  the pattern is in the key because `sync_file_links` reassigns it in place)
  instead of nulling the
  entire registry for that record type. **Residual — read this before
  upgrading:** everything that reads `file_registry` simply stops seeing the
  definition, so the affected file loses six behaviours at once, not just the
  one previously listed here:
  1. the `required=True, role=INPUT` "missing file" gate stops firing, so a
     record can no longer be marked `blocked` for it;
  2. it is never checksummed, so its `RecordFileLink` is never created and the
     file stays untracked;
  3. file-change triggers stop firing — a RecordFlow
     `file(x).on_update().invalidate_all_records(...)` silently dies;
  4. `clear_output_files` and `delete_record_cascade` no longer collect it, so
     the file is **left on disk** after the record is deleted;
  5. `ctx.files.resolve("name")` in a pipeline task raises `KeyError`, which
     retries and lands in the DLQ;
  6. the single-file download endpoint 404s for it.

  A WARNING is therefore not cosmetic. **A `LIKE '%{data.%'` scan is necessary
  but no longer sufficient** — this release adds two further config-load
  rejections (unknown placeholder, degenerate worst-case render), and a stored
  row failing either is skipped with the same six consequences while matching
  no `{data.` pattern. Before release, validate *every* stored pattern rather
  than grepping for one shape:

  ```python
  # against a copy of the deployment's DB
  from clarinet.files import validate_file_pattern
  for name, pattern, multiple in rows:  # SELECT name, pattern, multiple FROM filedefinition
      try:
          validate_file_pattern(pattern, is_collection=multiple)
      except ValueError as exc:
          print(f"{name}: {pattern!r} — {exc}")
  ```

  Every line printed is a definition that will be dropped from
  `RecordType.file_registry` after the upgrade.
- A rendered name containing a NUL byte is now refused by the containment
  check itself. `os.path.normpath` and `Path.is_relative_to` are pure string
  operations and passed it through, and `Path.is_file()` answers `False` for
  such a path rather than raising — so a poisoned `RecordFileLink.filename`
  was indistinguishable from a missing file and went unreported. Reachable
  from the persisted-filename path, which has no value guard upstream of the
  join.
- A `clarinet_storage_path` that is not absolute, or that carries a `..`
  component, is now refused at path-resolution time. A trailing slash or a
  doubled separator stays legal — neither enables traversal, and a stored row
  may already hold one. This is deliberately narrower than full
  containment: the field is *by design* a root disjoint from
  `settings.storage_path` (an admin-only per-record storage-root override, not
  a subdirectory selector), so no containment check applies — a well-formed
  absolute path set by an admin, or already present in the database, is still
  honoured. This residual is accepted, not an oversight.

### Added

- **Series-subset anonymization + multi-PACS C-STORE fan-out.**
  `AnonymizationService.anonymize_study(..., series_uids=[...])` restricts a run
  to an explicit series selection, with strict validation — empty / unknown /
  filter-excluded UIDs raise `AnonymizationFailedError` naming each offending
  UID. `AnonymizationOrchestrator.run` and `run_anonymization` pass
  `series_uids` through kwarg-only (never read from `msg.payload`). A subset
  run still persists the study-granular `anon_uid` but marks its Record data
  with `series_uids`, so the skip-guard treats it as not-done and a later
  whole-study run on the same record re-runs instead of being wrongly
  skipped. Separately, `settings.anon_extra_pacs_nodes` wires extra C-STORE
  destinations on every construction path (orchestrator/worker factory and
  the HTTP DI factory); per-node failure counts land in
  `AnonymizationResult.send_failed_by_node`, and the opt-in
  `settings.anon_fail_on_send_error` raises the new `AnonymizationSendError`
  before `study.anon_uid` persists.
- `find_records` (`ClarinetClient` and the pipeline sync wrapper) now logs a
  warning when a wide-scope call (no `series_uid`/`study_uid` filter) is
  truncated at the first cursor page, pointing the caller at `iter_records`.
- **RAM-lean `Image` reads (opt-in, additive).** `read`/`read_nifti`/`read_nrrd` take
  `load_data=False` (grid + `shape`/`has_data` from the header only — the #452 fix) and
  `dtype=` (cast once off-disk, no forced float64; `dtype=None` keeps float64 for
  filtering). New `read_slice()` (single 2-D slice), read-only `dataobj` proxy (NIfTI),
  and `unload()`/`close()`/context-manager for deterministic frees. `Segmentation`
  reads now route at uint8 (a mask never passes through float64 — observably identical).
- **`LayeredSegmentation`** — first-class 4-D overlapping-segment NRRD (Slicer format)
  over one shared 3-D grid: `from_layers().save()` (raw, Slicer-native interleaved 4-D,
  fill-in-place) and `read_header`/`read_layer`/`read_layer_slice`/`iter_layers`.
- **Opt-in correspondence engine in Slicer scripts + `SlicerHelper.detect_overlaps`.**
  `SlicerService.execute(..., include_correspondence=True)` prepends the image
  `correspondence/` engine (flattened from live source, numpy-only) into the script
  payload so it is callable inside Slicer; default `False` is unchanged. New
  non-destructive `SlicerHelper.detect_overlaps(seg_a, seg_b, *, resample=False)`
  returns one `{name_a, name_b, inter, size_a, size_b, dice, iou, centroid_distance_mm}`
  dict per overlapping segment pair (`[]` when disjoint or a source is empty), reusing
  the same reference-grid guards as `subtract_segmentations`; raises `SlicerHelperError`
  unless the bundle was included. Additive — no new dependency.
- **`clarinet.scripting` frame for downstream operational scripts.** New
  `@script` decorator + `ScriptCtx` (`from clarinet.scripting import script,
  ScriptCtx`) synthesize a single-command typer app with standard options
  `--commit`/`--limit`/`--yes`/`--api-base`, an `asyncio.run` bridge, tally
  summary, and exit codes (1 on recorded failures). Safe default: scripts are
  dry-run unless `--commit`. Lazy `ctx.client` builds a `ClarinetClient` from
  settings only when touched; the service token is never a CLI flag. `typer`
  becomes a hard dependency; a downstream-author doc page ships via
  `clarinet agent init/update`. Additive — no existing behavior changes.
- **Anonymization guide in the downstream agent-docs bundle.** `clarinet agent init|update`
  now installs `.claude/rules/clarinet/anonymization.md`: the `anonymize-study` RecordDef,
  wiring `anonymize_study_pipeline` into a flow, adding project fields via
  `run_anonymization(..., extra_record_data=...)`, the `record.data` success/skip/error
  branches, the skip-guard, the `anon_*` settings, and the `anon migrate-paths` /
  `anon scrub-db` operator commands. `workflows.md` § Built-in tasks gains
  `anonymize_study_pipeline` and `prefetch_dicom_web`, and now spells out that task-name
  collisions are on the **bare function name** (`{namespace}:{function_name}`, not
  module-qualified). The `research` project template ships the same doc.

### Improved

- Cross-flow imports now work in **both** sort directions (native module cache),
  and a flow file's `.call()` callbacks survive across a multi-file load — the
  per-file `call_function_registry.reset()` that erased earlier files' callbacks
  is fixed.

### Changed

- Hard invalidation (`POST /records/{id}/invalidate`, RecordFlow
  `invalidate_records()`) now always fires `on_status("pending")` flows —
  even when the record was already `pending`. Previously an already-pending
  record was reset silently and its flows never re-ran, so stale prefills
  survived re-invalidation. Downstream impact: every action reachable from
  `on_status("pending")` (and from flows without a status trigger) must be
  idempotent — it re-runs on every hard re-invalidation.
- The RecordFlow engine cuts invalidation cycles at runtime: a record whose
  flows are already dispatching in the current cascade is still invalidated,
  but its flows are skipped with an `Invalidation cycle detected` ERROR log.
  Mutually-invalidating flows remain a configuration error.
- `mode` on the invalidate endpoint and in `InvalidateRecordsAction` is now
  validated as `"hard" | "soft"` — a typo returns 422 / fails at flow
  definition instead of silently behaving like soft mode.
- `GET /api/pipelines/runs` now advertises a `[1, 2147483647]` (int32) bound on
  the `record_id` query filter — an out-of-range value returns 422 at the API
  boundary instead of reaching PostgreSQL as a `NumericValueOutOfRange`.
- Default `project_description` changed from `"Medical Imaging Framework"` to
  `"Imaging Research Framework"`, matching the framework's domain-agnostic
  framing (docs, agent rules, and the bundled demo no longer assume a medical
  use case). Projects that set `project_description` explicitly are unaffected.

### Fixed

- **`PATCH /types/{name}` no longer answers with an empty `file_registry`
  after a successful sync.** `sync_file_links(clear_existing=True)` deleted the
  old `RecordTypeFileLink` rows with `session.delete()` and then reassigned
  `record_type.file_links`; the deleted links were still in the loaded
  collection, and their remove events — through pydantic's value-based
  `__eq__` on link models — knocked the freshly inserted links out of the new
  collection. The rows were right, but the response was served from that same
  identity-mapped object and carried `"file_registry": []` until the next
  request. In TOML mode the same stale object fed the background
  `export_record_type_to_toml`, so every such PATCH also rewrote `{name}.toml`
  without its `[[file_registry]]` tables. Links now go through the relationship
  itself (`delete-orphan` cascade), so the response and the export list the
  files just synced. `POST /types` was never affected (#567).
  **Operator note (TOML mode):** re-save (PATCH) any record type whose
  registry was edited through the API while this bug was live. The DB links
  survived restarts — an absent `file_registry` key leaves them untouched — but
  a fresh bootstrap from those TOML files would create the types without links.
- **`Files.resolve` no longer renders a collection**, which turned a *legal*
  `multiple=True` pattern into a 500. Collections are exempt from the
  config-load render rules on the grounds that they glob rather than render —
  but `resolve` had no `multiple` branch (unlike `checksums`), so
  `{study_uid}/x.dcm` on a patient-level record rendered to `/x.dcm` and raised
  `UnsafePathError`. `build_slicer_context` loops `resolve` over the whole
  registry catching `(KeyError, ValueError)`, and `UnsafePathError` is
  deliberately neither, so `POST /slicer/records/{id}/open` failed uncaught for
  every user of that record. `Files.resolve` now raises `ValueError` naming the
  definition and pointing at `Files.glob()`, and — because that list is not a
  fallback but a hard `ScriptArgumentError` (422) — the Slicer context loop
  skips collections before reaching it, so a record type declaring one opens
  normally. `Files.exists` branches to `glob` for a collection, and
  `RecordQuery.file_path` reports one as `PipelineStepError` rather than
  letting a bare `ValueError` escape to TaskIQ.
  **Downstream note:** project code that called `ctx.files.resolve(name)` on a
  `multiple=True` definition previously received a meaningless path (every
  placeholder substituted, e.g. `slice_.dcm`) and now gets a `ValueError`. Such
  a call was already broken — it can only have resolved to a file that does not
  exist — but it fails loudly now rather than silently. Use `ctx.files.glob()`
  for collections.
- The demo's anonymization wrapper (`examples/demo`) no longer shadows the built-in
  task. It was named `anonymize_study_pipeline`, and `@pipeline_task` derives
  `task_name` as `{namespace}:{function_name}` — not module-qualified — so it
  registered under the same key as the framework built-in and `register_task()`
  raised `PipelineConfigError`. The demo only escaped this because `have_dicom`
  defaults to `false`; draining its own `dicom` queue requires a worker with
  `have_dicom = true`, which imports the built-in and collides, so the example could
  not run the task it demonstrates. Renamed to `anonymize_study_with_type`. The
  underlying collision-by-bare-name design is tracked in #466.
- RecordFlow patient-scope context is no longer silently truncated at the first
  cursor page. `RecordFlowEngine._get_record_context` and the
  `call_registered_callable` pipeline task aggregated records via
  `find_records(patient_id=..., limit=1000)`, which returns only the first page —
  for a patient with >1000 records everything past it was dropped, skewing
  condition and action evaluation. Both now page through all records via
  `iter_records`.
- `get_study_hierarchy` no longer silently caps the study's records at the
  first 1000 — it aggregates every record via `iter_records`.
- Project-template agent docs (`.claude/rules/workflows.md`) no longer document
  the `FileResolver` API removed in 0.7.0 — the `ctx.files` section is re-synced
  with the canonical `Files` facade docs shipped by `clarinet agent init`.
- **DICOM→NIfTI slice-axis orientation from ground-truth `ImagePositionPatient`
  (#453).** `read_dicom_series` now recomputes the slice-axis sense and origin from
  the first/last file's `ImagePositionPatient`
  (`clarinet.services.image.orientation.ground_truth_slice_geometry`) before
  canonicalization, instead of trusting SimpleITK's `GetDirection()` sign. On long
  axial series with sub-mm spacing wobble SimpleITK could return a slice-axis sign
  inconsistent with GDCM file order, producing an anatomically flipped volume.
  Correctly-read series are byte-identical — only affected series change on
  re-conversion. New `is_volume_misoriented(volume_nifti, dicom_dir)` detection
  primitive backs the per-project migration (`clarinet/docs/migration-orientation-0.10.17.md`).
- Pipeline audit rows for patient-less tasks (e.g. Quarto report renders) are no
  longer lost. `PipelineMessage` declares `patient_id`/`study_uid` as required
  `str`, so patient-less tasks carry `""` sentinels; `AuditMiddleware` forwarded
  them verbatim, the `''` patient id violated the patient FK, and the audit POST
  failed — no run row (the task itself was unaffected). The middleware now sends
  `NULL` for absent patient/study/series ids and `PipelineTaskRunCreate`
  re-normalizes `''` → `NULL` at the API boundary (covers stale workers
  mid-rolling-upgrade); the `GET /api/pipelines/runs` `patient_id` filter now
  treats `''` as absent. The empty patient-id path needs no backfill — those
  inserts always failed on the FK — but `''` `study_uid`/`series_uid` values
  from patient-level dispatches did insert historically: downstream projects
  should normalize legacy rows (`UPDATE pipeline_task_run SET study_uid = NULL
  WHERE study_uid = ''`, same for `series_uid`). A prod DB that dropped the
  patient FK as a stopgap must also normalize `''` patient ids before
  re-adding it.
- **Conform-on-export re-grids per layer; shared-layer segmentations
  preserved (#500).** `export_segmentation(conform_to=...)`'s `REARRANGED`
  repair previously read each segment through
  `arrayFromSegmentBinaryLabelmap`, which returns all-zero voxels for
  segments stored in a shared binary-labelmap layer — the normal
  representation of a loaded `.seg.nrrd` with non-overlapping segments — so a
  shared-layer (or mixed shared+separate) source lost some or all of its
  segments' voxels on conform-export, and custom label values did not
  survive even when voxels did. The repair now groups segments by their
  shared labelmap layer, resamples each layer's multi-label image onto the
  reference grid in one shot (nearest-neighbor — exact for the REARRANGED
  signed-permutation relation), and imports it via
  `ImportLabelmapToSegmentationNode`, which bakes each segment's label value
  into its own voxels; names and colors are restored by matching the baked
  value back to the source. This preserves every segment's voxels and custom
  label values for any layer representation — shared, separate, or mixed.
  Imported segment ids may differ from the source's (not part of the
  conformance contract; validators match by name). The post-write guard also
  tightened: it now fails closed on any single segment's voxel loss (matched
  by name, duplicate names compared by count), not just total loss — a
  mixed-representation source that previously lost only its shared-layer
  segments silently now raises instead. A source whose segments are *all*
  voxel-less has no layer to import, so the re-grid materializes the
  reference extent as an all-zero labelmap; without it Slicer's writer emits
  a degenerate 1×1×1 file and the post-write grid check deletes it.
- The record-type edit UI no longer strips file-definition fields on save. The
  form round-tripped only part of `FileDefinitionRead`, and the backend treats
  a submitted `file_registry` as authoritative, so every save nulled
  `grid_conform_to`, `on_grid_mismatch` and `level` on the global
  `FileDefinition` row and reset `allow_path_collision` on the binding — in
  TOML mode the background export then persisted the loss to disk. All ten
  read fields now round-trip, pinned by a parity test against
  `FileDefinitionRead.model_fields`.
- An `on_grid_mismatch="conform"` repair no longer risks the original OUTPUT.
  The repair is written to a hidden sibling temp file, re-read and
  re-classified from disk, and only then atomically moved over the original —
  a repair that fails, or lands on a still-mismatched grid, now 409s with the
  original bytes intact instead of having already overwritten them in place.
  The temp file is unique per repair — two concurrent repairs of one record
  no longer share it — and is removed on any failure, not only an
  `ImageError`: an orphaned dotfile would otherwise be matched by `Path.glob`
  in any overlapping collection pattern on every check-files run.
- A `conform` repair on the update paths (`PATCH /records/{id}/data`,
  `PATCH /records/{id}/submit`) now syncs stored output checksums and fires
  file-change triggers. Only the POST paths ran the post-commit output sync,
  so a PATCH-triggered repair left `RecordFileLink.checksum` describing the
  pre-repair bytes and no downstream file flow ever saw the mutation.
- OUTPUT grid-mismatch 409s now carry both grids' summaries (shape, spacing,
  origin, direction), matching what the INPUT side already reported — the
  message previously named only the `RelationKind`.
- Config load now rejects two grid-conformance declarations it used to accept:
  a `grid_conform_to` pointing at a file that itself declares one (chains and
  cycles — enforcement order is undefined and a repaired reference silently
  invalidates its dependents), and an `on_grid_mismatch` set without a
  `grid_conform_to` (the action could never run). An INPUT file referencing an
  OUTPUT of the same RecordType is legal but now logs a `WARNING` — the record
  stays blocked until that OUTPUT exists.
- Two RecordTypes can no longer disagree about a shared file. A
  `FileDefinition` row is shared by every type binding it and was upserted
  once per type in config order, so a type that bound `seg` without the
  `grid_conform_to` another type declared left the row in whichever state
  reconciled last, flipped it on every restart and silently switched the
  guard off — the #499 hole in config form (the same last-write-wins already
  applied to `pattern`, `description`, `multiple` and `level`). Config load
  now rejects the disagreement before any DB write, naming the file, the
  fields and both types. `POST`/`PATCH /types` fill the row-level fields a
  file entry omits from the stored row before validating and writing, so a
  partial entry from one type can no longer null another type's declaration,
  and a type binding a guarded file without its reference is rejected with a
  409 instead of blocking its records at runtime. An explicit change through
  one type still rewrites the shared row for every binder — now logged as a
  `WARNING` naming the file, the changed fields and the other binders; in
  TOML mode only the edited type is re-exported, so the next startup rejects
  the disagreement until the other types' TOML files match (follow-up: #564,
  re-validate and re-export sibling types).

## 0.7.0 — Post-submit edit locking (RecordType.editable / edit_window_days)

### Added

- `RecordType.editable` (bool, default `true`) — when `false`, finished
  records of the type cannot be changed by non-superusers. Every API path
  that could alter a submitted answer returns 409: `PATCH /data`,
  `PATCH /submit`, any status change of a finished record (`PATCH /status`,
  `PATCH /bulk/status`), and hard invalidation (`POST /invalidate`).
  Superusers (including pipeline service tokens) and in-process service
  calls (RecordFlow triggers) bypass the lock. Enforcement lives in
  `RecordService` (`acting_user` parameter; `None` = trusted caller) and
  raises `RecordEditLockedError` → 409.
- `RecordType.edit_window_days` (int | null, default `null`) — bounds
  re-editing of finished records to N days after `finished_at`; `null`
  disables the limit, `0` locks immediately at submit. Applies only while
  `editable` is `true`.
- `RecordRead.is_editable` (computed) — server-side editability verdict;
  the frontend record form and Re-submit button now honor it (superusers
  still see the edit UI).
- Both flags are settable in TOML and Python config modes
  (`RecordDef(..., editable=False, edit_window_days=30)`).

### Notes

- Schema change: new columns `recordtype.editable` (NOT NULL, server
  default `true`) and `recordtype.edit_window_days` (nullable) —
  downstream projects must generate an Alembic migration
  (`make db-migration && make db-upgrade`).
- Defaults preserve current behavior; no action needed unless you want to
  lock answers after submission.

## 0.6.0 — Opt-in user_id inheritance from parent records

### Breaking

- `POST /api/records` no longer inherits `user_id` from the parent record
  unconditionally. Inheritance now requires the created record's type to
  have the new `RecordType.inherit_user_from_parent` flag enabled (and no
  explicit `user_id` in the payload). Downstream projects relying on the
  implicit behavior must set `inherit_user_from_parent = true` on the
  affected record types in their config.
- Schema change: new boolean column `recordtype.inherit_user_from_parent`
  (NOT NULL, server default `false`) — downstream projects must generate
  an alembic migration (`make db-migration && make db-upgrade`).

### Notes

- RecordFlow's `inherit_user` flag is unaffected — it inherits from the
  *triggering* record (a separate axis from parent inheritance).
- Parent existence validation and the inheritance decision moved from the
  router into `RecordService.create_record`.
- An inherited `user_id` is re-checked against `unique_per_user` (the
  route-level constraint check runs before inheritance and cannot see it);
  a duplicate now returns 409 `UNIQUE_PER_USER`.
- The flag is settable in both config modes: TOML and Python
  (`RecordDef(..., inherit_user_from_parent=True)`).

## 0.3.0 — Per-project queue namespacing

### Breaking

- Pipeline queue names now include the project namespace:
  `{settings.pipeline_task_namespace}.{default,gpu,dicom,dead_letter}`,
  where `pipeline_task_namespace` is normalized from `settings.project_name`.
  For the default `project_name = "Clarinet"` the queues remain
  `clarinet.default`/`.gpu`/`.dicom`/`.dead_letter` — backward compatible.
  Projects with a custom `project_name` now get isolated queues
  (e.g. `liver.default`, `liver.gpu`, ...).
- Removed module-level constants `DEFAULT_QUEUE`, `GPU_QUEUE`, `DICOM_QUEUE`,
  `DLQ_QUEUE` from `clarinet.services.pipeline.broker`.  Use the new
  `settings.default_queue_name`, `settings.gpu_queue_name`,
  `settings.dicom_queue_name`, `settings.dlq_queue_name` properties instead.
- Removed `extract_routing_key()` — routing keys now equal the full queue
  name, eliminating the suffix-based scheme that caused cross-project
  collisions on a shared exchange.
- `get_broker()` is preserved as a backward-compat shim equivalent to
  `get_broker_for(settings.default_queue_name)`.  New code should use
  `get_broker_for(queue_name)` (per-queue broker registry).

### Added

- `clarinet.services.pipeline.get_broker_for(queue_name)` — per-queue
  broker registry; tasks are bound to the broker for their declared queue
  at decoration time, so `task.kicker().kiq()` always publishes to the
  correct queue.  Closes the H0 routing bug where tasks like
  `anonymize_study_pipeline` did not reach `clarinet.dicom`.
- `clarinet.services.pipeline.get_all_brokers()` — snapshot of created
  brokers (used by API lifespan to start/stop them all).
- `clarinet.services.pipeline.is_registered(queue_name)` — public check
  for whether a broker for *queue_name* has been created.
- `clarinet.services.pipeline.reset_brokers()` — clears the broker
  registry; the caller is responsible for shutting brokers down first
  (otherwise the open AMQP connection leaks).
- `clarinet.services.pipeline.load_task_modules()` — promoted from the
  worker-private `_load_task_modules`; used by both the worker and the
  API lifespan.
- `Pipeline.step(task, queue=...)` now raises `PipelineConfigError` if the
  explicit queue conflicts with the task's bound queue
  (`task._pipeline_queue`).  Previously this was silently re-routed
  through the wrong broker.
- `PipelineChainMiddleware._dispatch_next_step` validates that the next
  step's queue matches the registered task's bound queue and emits a
  `chain_failure` to the DLQ on mismatch.

### Migration notes

- **Workers AND the API server must be restarted** after upgrading to
  pick up the new queue names.  The API now imports flow files at
  startup (so it can dispatch via the right per-queue broker) — any
  exception in a flow file now fails API startup as well as worker
  startup.  Make sure flow files are import-safe.
- Old queues (e.g. `clarinet.default` on a project whose `project_name`
  is not `"Clarinet"`) will remain in RabbitMQ with stale messages —
  drain or delete them via the Management UI.
- Downstream projects: replace
  `from clarinet.services.pipeline import DEFAULT_QUEUE` (and friends)
  with `from clarinet.settings import settings; settings.default_queue_name`
  (and friends).  Confirm `project_name` in `settings.toml` reflects the
  intended namespace.
