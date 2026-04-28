# Changelog

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
- `clarinet.services.pipeline.reset_brokers()` — clears the broker
  registry; intended for tests.
- `Pipeline.step(task, queue=...)` now raises `PipelineConfigError` if the
  explicit queue conflicts with the task's bound queue
  (`task._pipeline_queue`).  Previously this was silently re-routed
  through the wrong broker.
- `PipelineChainMiddleware._dispatch_next_step` validates that the next
  step's queue matches the registered task's bound queue and emits a
  `chain_failure` to the DLQ on mismatch.

### Migration notes

- Workers must be restarted after upgrading to pick up the new queue
  names.  Old queues (e.g. `clarinet.default` on a project whose
  `project_name` is not `"Clarinet"`) will remain in RabbitMQ with stale
  messages — drain or delete them via the Management UI.
- Downstream projects: replace
  `from clarinet.services.pipeline import DEFAULT_QUEUE` (and friends)
  with `from clarinet.settings import settings; settings.default_queue_name`
  (and friends).  Confirm `project_name` in `settings.toml` reflects the
  intended namespace.
