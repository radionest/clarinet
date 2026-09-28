---
paths:
  - "clarinet/repositories/record_repository.py"
  - "clarinet/repositories/record_type_repository.py"
---

# RecordRepository & RecordTypeRepository — Specialized Methods

Deep reference: [Persistence conventions](../../docs/kb/persistence.md).

## RecordRepository Methods

Beyond `BaseRepository`, `RecordRepository` has:

### Read / fetch

| Method | Description |
|---|---|
| `get_with_record_type(id)` | Eager-loads `record_type` |
| `get_with_relations(id, *, lock=False, populate_existing=False)` | Eager-loads patient, study, series, record_type, file_links. `lock=True` adds `SELECT ... FOR UPDATE`. `populate_existing=True` re-reads the row over whatever the identity map holds — required after `write_transition`, whose Core UPDATE bypasses it |
| `get_all_with_relations(skip, limit)` | All records with full eager load |
| `get_all_for_user_roles(role_names, skip, limit)` | Records whose `RecordType.role_name` ∈ roles (NULL excluded — superuser-only) |
| `find_by_user(user_id, ...)` | Records for specific user |
| `find_pending_by_user(user_id)` | Pending/inwork records |
| `find_by_criteria(criteria)` | Complex search via `RecordSearchCriteria` (legacy, offset pagination) |
| `find_page(criteria, *, cursor, limit, sort)` | Cursor-based keyset pagination via `RecordSearchCriteria` |
| `find_random(criteria, *, for_update=False)` | Single random record (`ORDER BY random() LIMIT 1`) matching criteria. `for_update=True` adds `FOR UPDATE OF record SKIP LOCKED` (PG) so concurrent claim-from-pool callers can't select the same row (no-op on SQLite). `RecordSearchCriteria.exclude_ids: set[int]` excludes ids a pool pick must skip — `claim_random_from_pool` grows it with each lost race |
| `get_viewer_anon_uids(uids, patient_ids)` | `{patient_id: {uid: anon_uid}}` for masking the viewer lists. A raw UID resolves only for its own patient (else writing a foreign raw UID leaks its anon UID); a known anon UID maps to itself for every patient (else keep/drop reveals which studies share a patient). UIDs with no anon counterpart are absent. Queries in `_VIEWER_UID_CHUNK` batches (asyncpg bind-param cap) |
| `get_record_type(name, *, with_files=True)` | RecordType by name with `file_links` eagerly loaded (raises `RecordTypeNotFoundError`). `with_files=False` → `session.get` by PK (identity-map hit, no eager `file_links` — scalars only) |

### Mutations

| Method | Description |
|---|---|
| `create_with_relations(record)` | Create with eager load after commit |
| `write_transition(id, *, expected_status, expected_user_id, to, owner, data=None, reason=None) -> bool` | The only status/owner writer (except `UserRepository.clear_owned_records` on user deletion, which only nulls `user_id`): one conditional UPDATE on status and owner (`WHERE status = expected_status AND user_id IS NOT DISTINCT FROM expected_user_id`); `False` means the record changed since the caller decided, nothing written. Always writes the resulting `owner` (unchanged is harmless). Appends `reason` to `context_info` in SQL (newline-separated, never overwritten). Stamps `started_at`/`finished_at` only when the status *changes* to inwork/finished. Never commits — the caller owns the transaction. Bypasses the identity map (re-read with `get_with_relations(..., populate_existing=True)`) and the SSE capture |
| `update_data(id, data)` | Replace data only (prefill) — status and owner untouched |
| `update_fields(record_id, update_data)` | Update arbitrary fields from a dict |
| `set_files(record, matched_files, *, commit=True)` | Create `RecordFileLink` rows; builds fd_map internally from eager-loaded `record_type.file_links`. `commit=False` keeps the links in the caller's transaction (`RecordService._transition`) |
| `add_file_links(record, matched_files)` | Additive `set_files`: creates links only for unlinked definitions (DB-dedupe via SELECT), existing links/checksums untouched; appends to `record.file_links` in memory. Returns links created; PK race vs concurrent writer → rollback + in-place reload, returns 0 |
| `update_checksums(record, checksums)` | Update checksum on existing `RecordFileLink` rows (keys: `name` for singular, `name:filename` for collections) |
| `delete_output_file_links(record)` | Single SQL `DELETE` of OUTPUT file links (race-safe vs concurrent pipeline writers) |
| `append_context_info(id, note)` | Append `note` to `context_info` (newline-separated, never overwritten) — soft invalidation; hard invalidation appends inside `write_transition` instead |
| `get_user_with_roles(id)` | The user with roles loaded, for new-owner checks; raises `UserNotFoundError` (404) rather than surfacing the FK as a 500 |

### Cascade delete

| Method | Description |
|---|---|
| `collect_descendants(root_id, *, for_update=False)` | BFS-collect root + descendants with full eager load. `for_update=True` locks the whole subtree |
| `delete_records(record_ids, *, commit=True)` | Bulk SQL `DELETE` (relies on FK `ON DELETE CASCADE` for both `RecordFileLink` and `parent_record_id`). `commit=False` keeps the txn / locks open for the caller |

### Validation / counters

| Method | Description |
|---|---|
| `check_constraints(record_type_name, series_uid, study_uid, patient_id=, user_id=, parent_record_id=)` | Validate RecordType constraints: level-UID consistency, parent_required, max_records, and (when `patient_id` is given) `unique_by` via `ensure_unique_by` |
| `ensure_unique_by(record_type, *, user_id, parent_record_id, patient_id, study_uid, series_uid, exclude_record_id=None)` | Raise `RecordUniquePerUserError` if another record already matches on every selected `unique_by` partition in this DICOM-level context; no-op when `unique_by` is `None`. **Bound-tuple rule**: skipped entirely when `"user"` is a selected partition and `user_id` is `None` — an unassigned record's user axis isn't evaluable yet, so pools stay creatable; the check closes at claim/assign time via this same method with `user_id` bound. A `{"parent"}`-only type has no such gap and dedupes at creation. `exclude_record_id` excludes the record under evaluation from the match count — required at assignment time (`RecordService._check_unique_by`, used by assign/claim/submit-auto-assign) since the candidate row already exists; creation-time callers (`check_constraints`, and `RecordService.create_record`'s parent user_id-inheritance re-check) omit it since the row doesn't exist yet |
| `count_by_type_and_context(record_type_name, patient_id, study_uid, series_uid, level)` | Count records matching type at the given DicomQueryLevel context (PATIENT → patient_id, STUDY → study_uid, SERIES → series_uid) |
| `get_available_type_counts(user_id, exclude_unique_violations=False)` | Dict of available RecordType -> count (batch-loaded to avoid N+1); `exclude_unique_violations=True` drops unassigned records that would violate `unique_by` for this user |
| `count_available_pending_for_user(user_id, role_names)` | Count of claimable records (pending + unassigned, role-scoped, `unique_by`-aware via `_unique_by_violation_filter`). `role_names=None` → whole pool (superuser); `[]` → 0. Powers the admin-dashboard `Claimable` column |
| `get_status_counts()` | Global status counts |
| `get_per_type_status_counts()` | Status counts per type |
| `get_per_type_unique_users()` | Unique user count per type |

## Constraint predicates: pre-insert vs post-insert reuse

A count/EXISTS uniqueness or quota check written for creation (candidate row
absent) silently matches the candidate itself when reused after the row is
persisted (claim/assign/update) — `ensure_unique_by` did exactly this at
assignment time, producing listed-but-unclaimable records. Whenever such a
predicate is called with an already-persisted candidate row, it must take an
exclude-self parameter (`exclude_record_id`-style) and have a test asserting
the idempotent re-check case (re-validating an existing row passes).

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

`RecordService.invalidate_record(record_id, mode, source_record_id=None, reason=None, *, actor)`:
- **hard**: the `Restart` command through `_transition` — any status but `preparing`
  returns to `pending` (data and owner kept), reason appended in the same
  `write_transition` UPDATE, always fires even pending → pending
- **soft**: `RecordRepository.append_context_info` only — no status change, no flows
- Default note: `"Invalidated by record #{source_record_id}"`
- `context_info` is appended (newline-separated), never overwritten

## Destructive Operations (delete, cascade)

Reference implementation: `collect_descendants(root_id, for_update=True)` + `delete_records(ids, commit=False)`.

When implementing record/entity deletion with cascade:

- **Row locking**: pass `for_update=True` to `collect_descendants` (which calls `get_with_relations(lock=True)` and `with_for_update()` on the BFS query) — prevents races with concurrent status changes / data submissions
- **Single transaction**: collect descendants → lock → delete files → `delete_records(commit=False)` — all in one `async with session.begin()` block. Never delete files outside the transaction boundary
- **File cleanup**: wrap `Path.unlink()` in `try/except OSError` — files may already be missing (concurrent cleanup, manual removal). Log warnings, don't raise
- **Conflict detection**: if a record is `inwork` (actively being edited), return 409 Conflict rather than silently deleting. Check status **after** acquiring the lock
- **FK behaviour**: `delete_records` issues a single bulk SQL `DELETE` and relies on DB-level `ON DELETE CASCADE` on both `RecordFileLink` and `parent_record_id` (SQLite enforces this only when `PRAGMA foreign_keys=ON`, always set for file-based SQLite — see `clarinet/models/CLAUDE.md`) — no manual reverse-topological deletion needed. `collect_descendants` still walks the full subtree first, so the emitted `deleted` event lists every removed id even though the FK would also catch stragglers

## PatientRepository: auto_id Generation

`PatientRepository.create()` overrides the base `create()` to auto-assign `auto_id` via a
**monotonic counter** that never decreases (even after patient deletion):
- **PostgreSQL**: native `Sequence` (`patient_auto_id_seq`) — `nextval()`.
- **SQLite**: `AutoIdCounter` table (single-row counter, lazy-seeded from `MAX(auto_id)`).

Retries up to 3 times on `IntegrityError` (UNIQUE conflict) as a safety net.
If `auto_id` is explicitly provided, `_advance_counter()` advances the sequence/counter
to at least that value before inserting, preventing future collisions.
