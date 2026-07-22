---
okf_version: "0.1"
---

# Clarinet knowledge base

Durable, cross-cutting knowledge about the framework. Operational instructions
(commands, worktree rules, checklists) stay in `CLAUDE.md`; file-specific
reference stays in the path-scoped `.claude/rules/`.

# Core

* [Backend architecture](./architecture.md) - How a request flows through routers, services and repositories, what the application lifespan builds, and the async rules that constrain every layer.
* [Domain model](./domain-model.md) - The Patient/Study/Series/Record hierarchy, what a RecordType declares, the record status lifecycle, and how record data, files and audit events hang off a record.
* [RecordType flags and uniqueness](./record-types.md) - The behavioural flags a RecordType declares — unique_by partitions, shared_editing, edit locking and record caps — and how their semantics compose.
* [Persistence conventions](./persistence.md) - How to write SQLModel models, repositories and migrations here — schema naming, eager loading, the server_default rule for additive migrations, and the pitfalls that only surface on PostgreSQL.

# Project configuration

* [Project configuration and the clarinet_plan package](./plan-package.md) - How a downstream project declares record types and custom Python code, how those files are imported through the single clarinet_plan anchor, and why loading fails fast.

# Automation

* [RecordFlow workflow engine](./recordflow.md) - The event-driven DSL that creates, updates and invalidates records when statuses change, data is submitted or files move — triggers, actions, evaluation context and invalidation semantics.
* [Pipeline task queue](./pipeline.md) - The TaskIQ + RabbitMQ distributed task system — per-queue brokers, the pipeline_task decorator and its TaskContext, DB-backed chain advancement, retry/DLQ policy and run auditing.

# Imaging and storage

* [Files and the anonymized-path contract](./files-and-anonymization.md) - Why Files is the only way to turn a record into a path on disk, and why resolvers raise AnonPathError instead of falling back to raw DICOM UIDs.
* [Imaging stack](./imaging-stack.md) - How Clarinet talks to imaging systems — the async DICOM client against PACS, the DICOMweb proxy and its four-tier cache behind OHIF, and the HTTP integration with 3D Slicer.
