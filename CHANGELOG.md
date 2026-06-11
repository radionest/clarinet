# Changelog

## Unreleased

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
