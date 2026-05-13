---
paths:
  - "clarinet/repositories/record_repository.py"
  - "clarinet/repositories/record_type_repository.py"
---

# RecordRepository & RecordTypeRepository — Specialized Methods

## RecordRepository Methods

Beyond `BaseRepository`, `RecordRepository` has:

### Read / fetch

| Method | Description |
|---|---|
| `get_with_record_type(id)` | Eager-loads `record_type` |
| `get_with_relations(id, *, lock=False)` | Eager-loads patient, study, series, record_type, file_links. `lock=True` adds `SELECT ... FOR UPDATE` |
| `get_all_with_relations(skip, limit)` | All records with full eager load |
| `get_all_for_user_roles(role_names, skip, limit)` | Records whose `RecordType.role_name` ∈ roles (NULL excluded — superuser-only) |
| `find_by_user(user_id, ...)` | Records for specific user |
| `find_pending_by_user(user_id)` | Pending/inwork records |
| `find_by_criteria(criteria)` | Complex search via `RecordSearchCriteria` (legacy, offset pagination) |
| `find_page(criteria, *, cursor, limit, sort)` | Cursor-based keyset pagination via `RecordSearchCriteria` |
| `find_random(criteria)` | Single random record (`ORDER BY random() LIMIT 1`) matching criteria |
| `get_record_type(name)` | RecordType by name with `file_links` eagerly loaded (raises `RecordTypeNotFoundError`) |

### Mutations

| Method | Description |
|---|---|
| `create_with_relations(record)` | Create with eager load after commit |
| `update_status(id, status)` | Status transition with validation |
| `update_data(id, data, new_status)` | Update data and optionally status |
| `update_fields(record_id, update_data)` | Update arbitrary fields from a dict |
| `set_files(record, matched_files)` | Create `RecordFileLink` rows; builds fd_map internally from eager-loaded `record_type.file_links` |
| `update_checksums(record, checksums)` | Update checksum on existing `RecordFileLink` rows |
| `delete_output_file_links(record)` | Single SQL `DELETE` of OUTPUT file links (race-safe vs concurrent pipeline writers) |
| `assign_user(id, user_id)` | Assign record to user |
| `unassign_user(id)` | Remove user; inwork -> pending |
| `ensure_user_assigned(id, user_id)` | Assign user only if record has no user yet |
| `claim_record(id, user_id)` | Claim unassigned record |
| `bulk_update_status(ids, status)` | Batch status update |
| `fail_record(id, reason)` | `status -> failed` + appends `"Manually failed: {reason}"` to `context_info` |

### Cascade delete

| Method | Description |
|---|---|
| `collect_descendants(root_id, *, for_update=False)` | BFS-collect root + descendants with full eager load. `for_update=True` locks the whole subtree |
| `delete_records(record_ids, *, commit=True)` | Bulk SQL `DELETE` (relies on FK `ON DELETE CASCADE` for `RecordFileLink`, `SET NULL` for `parent_record_id`). `commit=False` keeps the txn / locks open for the caller |

### Validation / counters

| Method | Description |
|---|---|
| `validate_parent_record(parent_id)` | Validate parent record exists and return it (for user_id inheritance) |
| `check_constraints(record, record_type)` | Validate RecordType constraints |
| `count_by_type_and_context(name, patient_id, study_uid, series_uid, level)` | Count records matching type at the given DicomQueryLevel context (PATIENT → patient_id, STUDY → study_uid, SERIES → series_uid) |
| `count_user_records_for_context(user_id, name, patient_id, study_uid, series_uid, level)` | Count user's records for unique-per-user constraint at given DicomQueryLevel |
| `get_available_type_counts(user_id)` | Dict of available RecordType -> count (batch-loaded to avoid N+1) |
| `get_status_counts()` | Global status counts |
| `get_per_type_status_counts()` | Status counts per type |
| `get_per_type_unique_users()` | Unique user count per type |

## RecordTypeRepository Methods

`RecordTypeRepository` overrides `BaseRepository` so that every read eagerly loads
`file_links → file_definition` (helper `_file_links_eager_load()`):

| Method | Description |
|---|---|
| `get(name)` | Get by primary key (name) with eager `file_links`. Raises `RecordTypeNotFoundError` |
| `get_all(skip, limit, **filters)` | All RecordTypes with eager `file_links` |
| `list_all(**filters)` | Same as `get_all` without pagination |
| `find(criteria: RecordTypeFind)` | Search by criteria (returns sequence) |
| `ensure_unique_name(name)` | Raises `RecordTypeAlreadyExistsError` if name is taken |

## Record Invalidation

`invalidate_record(record_id, mode, source_record_id=None, reason=None)`:
- **hard**: `status` -> `pending`, append reason to `context_info` (keeps `user_id`)
- **soft**: only append reason to `context_info` (status unchanged)
- Default reason: `"Invalidated by record #{source_record_id}"`
- `context_info` is appended (newline-separated), never overwritten

## Destructive Operations (delete, cascade)

Reference implementation: `collect_descendants(root_id, for_update=True)` + `delete_records(ids, commit=False)`.

When implementing record/entity deletion with cascade:

- **Row locking**: pass `for_update=True` to `collect_descendants` (which calls `get_with_relations(lock=True)` and `with_for_update()` on the BFS query) — prevents races with concurrent status changes / data submissions
- **Single transaction**: collect descendants → lock → delete files → `delete_records(commit=False)` — all in one `async with session.begin()` block. Never delete files outside the transaction boundary
- **File cleanup**: wrap `Path.unlink()` in `try/except OSError` — files may already be missing (concurrent cleanup, manual removal). Log warnings, don't raise
- **Conflict detection**: if a record is `inwork` (actively being edited), return 409 Conflict rather than silently deleting. Check status **after** acquiring the lock
- **FK behaviour**: `delete_records` issues a single bulk SQL `DELETE` and relies on DB-level `ON DELETE CASCADE` (`RecordFileLink`) and `ON DELETE SET NULL` (`parent_record_id`) — no manual reverse-topological deletion needed

## PatientRepository: auto_id Generation

`PatientRepository.create()` overrides the base `create()` to auto-assign `auto_id` via a
**monotonic counter** that never decreases (even after patient deletion):
- **PostgreSQL**: native `Sequence` (`patient_auto_id_seq`) — `nextval()`.
- **SQLite**: `AutoIdCounter` table (single-row counter, lazy-seeded from `MAX(auto_id)`).

Retries up to 3 times on `IntegrityError` (UNIQUE conflict) as a safety net.
If `auto_id` is explicitly provided, `_advance_counter()` advances the sequence/counter
to at least that value before inserting, preventing future collisions.
