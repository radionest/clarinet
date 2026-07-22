---
type: Concept
title: RecordType flags and uniqueness
description: The behavioural flags a RecordType declares — unique_by partitions, shared_editing, edit locking and record caps — and how their semantics compose.
tags: [domain, records, uniqueness, configuration]
timestamp: 2026-07-22T04:35:40Z
---

A `RecordType` is the project's declaration of one kind of work: it names the
JSON Schema for the record's data, the role allowed to do it, the files it
consumes and produces, and optional 3D Slicer scripts — see
[Domain model](./domain-model.md) for where it sits in the hierarchy and
[The clarinet_plan package](./plan-package.md) for how projects declare types
in TOML or Python. This page covers the behavioural flags and how they compose.

## Flags

| Flag | Effect |
|---|---|
| `unique_by` | uniqueness partition set, a subset of `{"user", "parent"}` (default both). At most one record per partition tuple, scoped within the type's own DICOM level. `None` disables uniqueness; an empty set is rejected |
| `shared_editing` | any role-holder may edit any record of the type; each edit reassigns ownership to the editor. Requires `'user' not in unique_by` |
| `editable` / `edit_window_days` | when false or expired, non-superusers get 409 on mutating a finished record |
| `inherit_user_from_parent` | a created child inherits `user_id` from its `parent_record_id` when no explicit user is given |
| `parent_required` | creation without `parent_record_id` returns 409 `PARENT_REQUIRED` |
| `max_records` | hard cap per DICOM-level context; exceeding it raises `RecordLimitReachedError`. `max_records=0` is the deprecation sentinel — blocks new records while keeping the type registered |
| `min_records` | advisory only — surfaced in admin stats, enforced nowhere |

## `unique_by` and `max_records` are orthogonal

`max_records` caps how many records may coexist at a level *in total*,
regardless of partition; `unique_by` only says a given partition tuple holds at
most one. So `unique_by={"parent"}` with `max_records=4` allows four coexisting
records, one per distinct parent — raising the cap does not loosen the
per-parent dedup, and narrowing the partition does not loosen the cap. A plain
one-per-level singleton is `unique_by=None` plus `max_records=1`.

## Bound-tuple rule

When `"user"` is a selected partition but the candidate `user_id` is still
`None`, the check is skipped — an unassigned record's user axis is not
evaluable yet, so unassigned pools stay creatable. The invariant closes at
claim/assign time, when the same check runs with `user_id` bound. A type
partitioned only by `{"parent"}` has no such gap.

## Deprecated: `unique_per_user`

The `unique_per_user=True/False` kwarg still works in config: it translates to
`{"user"}` / `None`, emits a `DeprecationWarning`, and is ignored when
`unique_by` is also given.
