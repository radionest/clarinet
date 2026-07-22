# Update Log

## 2026-07-22

* **Creation**: [RecordType flags and uniqueness](./record-types.md) — split out of the domain model page, which had crossed the size guideline; the flags and uniqueness semantics are what config authors need on their own.
* **Update**: [Domain model](./domain-model.md) — RecordType flags moved to their own page; the parent-link `ON DELETE CASCADE` detail folded into the hierarchy section.
* **Update**: [Backend architecture](./architecture.md) — dropped the rotting subclass count from the exception flow and added the missing `init-migrations` CLI entry.

## 2026-07-21

* **Initialization**: [Backend architecture](./architecture.md) — layered design, exception flow, the shared-`AsyncSession` concurrency rule and the ordered application lifespan, gathered from the root and `clarinet/` CLAUDE.md files so the startup ordering constraints live in one place.
* **Initialization**: [Domain model](./domain-model.md) — the entity hierarchy, RecordType flags, the `preparing`/`blocked`/`pause` lifecycle and the data-vs-`context_info` split, which were previously spread across `models/`, `api/` and two rule files.
* **Initialization**: [Persistence conventions](./persistence.md) — model and repository conventions plus the additive-migration `server_default` rule; captured because the failure mode is invisible on SQLite and only appears against PostgreSQL.
* **Initialization**: [Project configuration and the clarinet_plan package](./plan-package.md) — config modes, the single-import-root design and the fail-fast contract; recorded with the reasoning behind the no-`sys.path` rule.
* **Initialization**: [RecordFlow workflow engine](./recordflow.md) — triggers, actions, tree-filtered evaluation context and invalidation semantics, including why every action reachable from `on_status('pending')` must be idempotent.
* **Initialization**: [Pipeline task queue](./pipeline.md) — queue namespacing, the `pipeline_task` contract, DB-backed chain advancement and the never-retry-4xx policy.
* **Initialization**: [Files and the anonymized-path contract](./files-and-anonymization.md) — the `Files` facade and the safe-by-default anonymized-UID rule; split into its own page because the backend/UX distinction is referenced from the DICOM, DICOMweb, pipeline, Slicer and model layers alike.
* **Initialization**: [Imaging stack](./imaging-stack.md) — DICOM client, the DICOMweb four-tier cache and the 3D Slicer integration including the `__execResult` merge contract.
