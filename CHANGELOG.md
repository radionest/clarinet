# Changelog

## Unreleased

### Breaking

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
- **RecordType `unique_per_user` default now heals to `True` on reconcile
  (#389).** The column `server_default` was aligned `false()`→`true()` to match
  the model default, and the config reconciler now heals an unset flag toward its
  concrete default on restart. **Downstream migration:** a config-managed type
  whose DB `unique_per_user` was backfilled to `False` and that does not set the
  flag explicitly is healed to `True` on first restart; if it already has
  multiple records per user, new record creation returns 409 `UNIQUE_PER_USER`.
  Set `unique_per_user=False` explicitly in that type's config to keep the old
  behavior. Every heal is logged.

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

### Fixed

- RecordFlow patient-scope context is no longer silently truncated at the first
  cursor page. `RecordFlowEngine._get_record_context` and the
  `call_registered_callable` pipeline task aggregated records via
  `find_records(patient_id=..., limit=1000)`, which returns only the first page —
  for a patient with >1000 records everything past it was dropped, skewing
  condition and action evaluation. Both now page through all records via
  `iter_records`.

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
