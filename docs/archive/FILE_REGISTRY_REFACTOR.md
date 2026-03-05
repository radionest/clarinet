# FILE_REGISTRY_REFACTOR.md — Remaining Refactoring Items

## 1. DRY Violations

### 1.1 selectinload block repeated 5 times (HIGH)
**File:** `src/repositories/record_repository.py:119-122, 144-147, 172-175, 203-206, 526-529`

The same 4-line eager-loading options block is copy-pasted 5 times:
```python
selectinload(Record.patient),
selectinload(Record.study),
selectinload(Record.series),
selectinload(Record.record_type),
```

**Fix:** Extract a module-level constant:
```python
RECORD_EAGER_LOAD = [
    selectinload(Record.patient),
    selectinload(Record.study),
    selectinload(Record.series),
    selectinload(Record.record_type),
]
# usage: .options(*RECORD_EAGER_LOAD)
```

### 1.3 Action routing duplication in flow_record.py (MEDIUM)
Five action-methods contain identical 4-line routing block:
```python
if self._current_condition:
    self._current_condition.add_action(action)
else:
    self.actions.append(action)
```

**Fix:** Extract `_add_action(action)` helper method.

### 1.6 Frontend decoder duplication (MEDIUM)
**File:** `src/frontend/src/api/records.gleam`

`record_type_base_decoder` and `record_type_full_decoder` share ~80% identical field parsing. Level string parsing also duplicated.

**Fix:** Extract common field decoder and `parse_level()` function.

---

## 2. KISS Violations

### 2.1 Defensive status enum type checking (LOW)
**File:** `src/services/recordflow/engine.py:121-123`
```python
current_status = (
    record.status.value if hasattr(record.status, "value") else record.status
)
```
`RecordRead.status` is always `RecordStatus` (enum). This defensive code hides a potential bug.

**Fix:** Use `record.status.value` directly.

---

## 3. YAGNI Violations

### 3.1 `on_created()` is a no-op for entity flows (LOW)
**File:** `src/services/recordflow/flow_record.py:125-134`

`on_created()` returns `self` without setting any state. Entity trigger is set in the constructor. Method is misleading.

**Fix:** Remove or add a deprecation comment.

### 3.2 Unused computed fields in Gleam Record type (LOW)
**File:** `src/frontend/src/api/models.gleam`

`study_anon_uid`, `series_anon_uid`, `clarinet_storage_path`, `radiant`, `slicer_*_formatted` declared but always set to `None` in decoders (`records.gleam:204-223`).

**Fix:** Either decode from API response or remove from Gleam type.

### 3.3 Unused `old_status` parameter (LOW)
**File:** `src/services/recordflow/engine.py:94`

`old_status` marked `# noqa: ARG002` — never used. If it's for future use, add a TODO.

---

## 4. Performance Bottlenecks

### 4.1 `bulk_update_status()` is O(n) queries (HIGH)
**File:** `src/repositories/record_repository.py:345-358`

Loops through `record_ids`, issuing one `SELECT` per record:
```python
for record_id in record_ids:
    record = await self.get_optional(record_id)
    if record:
        record.status = new_status
```

For 100 records = 100 queries. Note: the loop is needed because the `set_record_timestamps` event listener fires on attribute set, which wouldn't trigger with a bulk `UPDATE`. However, timestamps can be set in the same bulk statement.

**Fix:** Use a single `UPDATE` statement with `CASE` for timestamps:
```python
stmt = (
    update(Record)
    .where(col(Record.id).in_(record_ids))
    .values(status=new_status)
)
await self.session.execute(stmt)
```
If the event listener for timestamps is critical, handle it via `CASE WHEN` or a DB trigger.

### 4.2 Sequential checksum computation (MEDIUM)
**File:** `src/utils/file_checksums.py:56-68`

`compute_checksums()` awaits each file sequentially. For records with many files, this is slower than necessary.

**Fix:** Use `asyncio.gather()` to parallelize I/O across files.

---

## 5. Architectural Consistency Issues

### 5.1 `checksums_changed()` ignores deletions (MEDIUM)
**File:** `src/utils/file_checksums.py:73-91`

Only detects new/changed files. Keys present in `old` but absent in `new` (deleted files) are not reported.

**Fix:** Add deletion detection:
```python
for key in old:
    if key not in new:
        changed.add(key)
```

### 5.2 `trigger_recordflow()` doesn't handle file change triggers (LOW)
**File:** `src/api/routers/record.py:73-95`

The shared helper handles status and data triggers but not file changes. The `check_record_files` endpoint duplicates the engine-access pattern inline.

**Fix:** Extend `trigger_recordflow()` to accept a trigger type enum, or extract a simpler `get_recordflow_engine(request)` utility.

### 5.3 Missing `role` field in example config (LOW)
**File:** `examples/demo/tasks/air_volume.json`

`file_registry` entry lacks explicit `"role": "input"`. Relies on default (`"output"`), which is wrong for an input file.

**Fix:** Add `"role": "input"` explicitly.

---

## 6. Test Coverage Gaps

| Gap | File | Severity |
|-----|------|----------|
| No large file (>64KB) checksum test | `tests/test_file_checksums.py` | Low |
| No file permission error handling test | `tests/test_file_checksums.py` | Low |
| No deleted file detection test | `tests/test_file_checksums.py` | Medium |
| No Gleam decoder unit tests | `src/frontend/` | Medium |
| Integration test only covers empty input_files | `tests/integration/test_record_working_folder.py` | Medium |

---

## 7. Summary by Priority

| # | Issue | Type | Severity | Files |
|---|-------|------|----------|-------|
| 4.1 | O(n) queries in bulk_update_status | Performance | HIGH | record_repository.py |
| 1.1 | selectinload repeated 5x | DRY | HIGH | record_repository.py |
| 5.1 | Deletion detection missing | Architecture | MEDIUM | file_checksums.py |
| 4.2 | Sequential checksum I/O | Performance | MEDIUM | file_checksums.py |
| 1.3 | Action-to-container duplication | DRY | MEDIUM | flow_record.py |
| 1.6 | Frontend decoder duplication | DRY | MEDIUM | records.gleam |
| 5.3 | Missing role in example | Architecture | LOW | air_volume.json |
| 3.1 | No-op on_created() | YAGNI | LOW | flow_record.py |
| 2.1 | Defensive enum check | KISS | LOW | engine.py |
| 3.2 | Unused Gleam computed fields | YAGNI | LOW | models.gleam |
| 3.3 | Unused old_status parameter | YAGNI | LOW | engine.py |
| 5.2 | trigger_recordflow() incomplete | Architecture | LOW | record.py |
