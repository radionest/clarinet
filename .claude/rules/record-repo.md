---
paths:
  - "clarinet/repositories/record_repository.py"
  - "clarinet/repositories/record_type_repository.py"
---

# RecordRepository & RecordTypeRepository — Specialized Methods

## RecordRepository Methods

Beyond `BaseRepository`, `RecordRepository` has:

| Method | Description |
|---|---|
| `get_with_record_type(id)` | Eager-loads `record_type` |
| `get_with_relations(id)` | Eager-loads patient, study, series, record_type |
| `get_all_with_relations(skip, limit)` | All records with full eager load |
| `find_by_criteria(criteria)` | Complex search via `RecordSearchCriteria` (legacy, uses offset pagination) |
| `find_page(criteria, *, cursor, limit, sort)` | Cursor-based keyset pagination via `RecordSearchCriteria` |
| `find_by_user(user_id, ...)` | Records for specific user |
| `find_pending_by_user(user_id)` | Pending/inwork records |
| `create_with_relations(record)` | Create with eager load after commit |
| `update_status(id, status)` | Status transition with validation |
| `update_data(id, data, new_status)` | Update data and optionally status |
| `set_files(record, matched_files)` | Create `RecordFileLink` rows; builds fd_map internally from eager-loaded `file_links` |
| `update_checksums(record, checksums)` | Update checksum on existing `RecordFileLink` rows |
| `assign_user(id, user_id)` | Assign record to user |
| `unassign_user(id)` | Remove user; inwork -> pending |
| `ensure_user_assigned(id, user_id)` | Assign user only if record has no user yet |
| `claim_record(id, user_id)` | Claim unassigned record |
| `bulk_update_status(ids, status)` | Batch status update |
| `validate_parent_record(parent_id)` | Validate parent record exists and return it (for user_id inheritance) |
| `check_constraints(record, record_type)` | Validate RecordType constraints |
| `get_available_type_counts(user_id)` | Dict of available RecordType -> count (batch-loaded to avoid N+1) |
| `get_status_counts()` | Global status counts |
| `get_per_type_status_counts()` | Status counts per type |
| `get_per_type_unique_users()` | Unique user count per type |

## Record Invalidation

`invalidate_record(record_id, mode, source_record_id=None, reason=None)`:
- **hard**: `status` -> `pending`, append reason to `context_info` (keeps `user_id`)
- **soft**: only append reason to `context_info` (status unchanged)
- Default reason: `"Invalidated by record #{source_record_id}"`
- `context_info` is appended (newline-separated), never overwritten

## PatientRepository: auto_id Generation

`PatientRepository.create()` overrides the base `create()` to auto-assign `auto_id = MAX(auto_id) + 1`
when `entity.auto_id is None`. Retries up to 3 times on `IntegrityError` (UNIQUE conflict).
If `auto_id` is explicitly provided, falls through to `super().create()` without the MAX query.
