# FILE_REGISTRY_REFACTOR.md — Code Review & Refactoring Plan

## Context

Branch `feature/file-registry` introduces a file registry system for Records: unified `file_registry` JSON column on RecordType, typed file accessor, SHA256 change detection, and RecordFlow integration. This review identifies DRY/KISS/YAGNI violations, performance bottlenecks, function purity issues, and architectural inconsistencies across all changed files.

---


                                                                             
  В файле src/services/recordflow/flow_record.py класс FlowRecord реализует DSL 
  для декларативного описания воркфлоу. Пять action-методов — add_record (стр.  
  212-215), update_record (стр. 234-237), call (стр. 264-267),
  invalidate_records (стр. 298-301), pipeline (стр. 323-326) — содержат
  идентичный четырёхстрочный блок маршрутизации action:

  if self._current_condition:
      self._current_condition.add_action(action)
  else:
      self.actions.append(action)



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

### 1.2 Triple handler duplication in RecordFlowEngine (HIGH)
**File:** `src/services/recordflow/engine.py:91-181`

`handle_record_status_change`, `handle_record_data_update`, `handle_record_file_change` share identical structure: check flows registry -> get context -> filter by trigger type -> execute. Only the filter predicate differs.

**Fix:** Extract `_dispatch_flows(record, predicate)`:
```python
async def _dispatch_flows(
    self, record: RecordRead, trigger_label: str,
    predicate: Callable[[FlowRecord], bool],
) -> None:
    record_type_name = record.record_type.name
    if record_type_name not in self.flows:
        logger.debug(f"No flows registered for record type '{record_type_name}'")
        return
    record_context = await self._get_record_context(record)
    for flow in self.flows[record_type_name]:
        if predicate(flow):
            logger.info(f"Executing {trigger_label} flow for '{record_type_name}' (id={record.id})")
            await self._execute_flow(flow, record, record_context)
```

### 1.4 Dict-to-FileDefinition conversion in two places (MEDIUM)
**Files:** `src/services/file_accessor.py:41-42` vs `src/services/file_validation.py:92-100`

Two different approaches to handling deserialized dicts from JSON:
- `file_accessor.py` uses `FileDefinition.model_validate(fd)`
- `file_validation.py` manually extracts fields via `.get()`

**Fix:** Add `ensure_file_definition()` utility in `src/models/file_schema.py`:
```python
def ensure_file_definition(fd: FileDefinition | dict) -> FileDefinition:
    return FileDefinition.model_validate(fd) if isinstance(fd, dict) else fd
```

### 1.5 Duplicate test fixtures across 3 test files (MEDIUM)
**Files:** `tests/test_file_validation.py`, `tests/test_file_accessor.py`, `tests/test_file_checksums.py`

Identical `mock_record` fixture duplicated in all three files.

**Fix:** Move to `tests/conftest.py` or a shared `tests/fixtures/` module.

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
`RecordRead.status` is always `RecordStatus` (enum). If it's sometimes a string, that's a contract violation in the model, not the engine's problem. This defensive code hides a potential bug.

**Fix:** Use `record.status.value` directly (or `record.status` if comparing with string triggers).

### 2.2 `get_file_accessor()` factory is unnecessary (LOW)
**File:** `src/services/file_accessor.py:101-114`

The factory function just wraps `RecordFileAccessor(record, working_folder)` without any logic. It's not registered as a DI dependency. Callers can use the constructor directly.

**Fix:** Remove or justify (e.g., register as DI dependency if needed).

### 2.3 `validate_record_files()` dual-None return (LOW)
**File:** `src/api/routers/record.py:98-127`

Returns `None` in two cases (no input_files, no working_folder), forcing callers to check `if result and result.matched_files`. Could be simplified to always return a `FileValidationResult`.

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

### 4.3 No glob caching in `RecordFileAccessor.__getattr__` (LOW)
**File:** `src/services/file_accessor.py:45-67`

Each attribute access re-globs the filesystem. If `accessor.lung_mask` is called in a loop, disk I/O is repeated.

**Fix:** Add lazy caching dict or document single-access assumption.

---

## 5. Function Purity Issues

### 5.1 `path_for()` creates directories as side effect (MEDIUM)
**File:** `src/services/file_accessor.py:79-94`

Name suggests it returns a path; it also creates parent directories. Callers may not expect disk mutation.

**Fix:** Rename to `ensure_path_for()` or split into pure `path_for()` + side-effect `ensure_dir_for()`.

### 5.2 `compute_checksums()` accesses private members (MEDIUM)
**File:** `src/utils/file_checksums.py:57-65`

Directly accesses `accessor._registry[name]`, `accessor._glob(fd)`, `accessor._resolve(fd)` — private API.

**Fix:** Add public methods to `RecordFileAccessor`:
```python
def get_definition(self, name: str) -> FileDefinition
def resolve_paths(self, name: str) -> list[Path]  # handles both singular and collection
```

---

## 6. Architectural Consistency Issues

### 6.1 `checksums_changed()` ignores deletions (MEDIUM)
**File:** `src/utils/file_checksums.py:73-91`

Only detects new/changed files. Keys present in `old` but absent in `new` (deleted files) are not reported.

**Fix:** Add deletion detection:
```python
for key in old:
    if key not in new:
        changed.add(key)
```

### 6.2 `trigger_recordflow()` doesn't handle file change triggers (LOW)
**File:** `src/api/routers/record.py:73-95`

The shared helper handles status and data triggers but not file changes. The `check_record_files` endpoint (line 457-459) duplicates the engine-access pattern inline.

**Fix:** Extend `trigger_recordflow()` to accept a trigger type enum, or extract a simpler `get_recordflow_engine(request)` utility.

### 6.3 Missing `role` field in example config (LOW)
**File:** `examples/demo/tasks/air_volume.json`

`file_registry` entry lacks explicit `"role": "input"`. Relies on default (`"output"`), which is wrong for an input file.

**Fix:** Add `"role": "input"` explicitly.

---

## 7. Test Coverage Gaps

| Gap | File | Severity |
|-----|------|----------|
| No large file (>64KB) checksum test | `tests/test_file_checksums.py` | Low |
| No file permission error handling test | `tests/test_file_checksums.py` | Low |
| No deleted file detection test | `tests/test_file_checksums.py` | Medium |
| No `_` prefix AttributeError test | `tests/test_file_accessor.py` | Low |
| No Gleam decoder unit tests | `src/frontend/` | Medium |
| Integration test only covers empty input_files | `tests/integration/test_record_working_folder.py` | Medium |

---

## 8. Summary by Priority

| # | Issue | Type | Severity | Files |
|---|-------|------|----------|-------|
| 4.1 | O(n) queries in bulk_update_status | Performance | HIGH | record_repository.py |
| 1.1 | selectinload repeated 5x | DRY | HIGH | record_repository.py |
| 1.2 | Triple handler duplication | DRY | HIGH | engine.py |
| 5.2 | Private member access in checksums | Purity | MEDIUM | file_checksums.py, file_accessor.py |
| 6.1 | Deletion detection missing | Architecture | MEDIUM | file_checksums.py |
| 4.2 | Sequential checksum I/O | Performance | MEDIUM | file_checksums.py |
| 1.4 | Dict-to-FileDefinition inconsistency | DRY | MEDIUM | file_accessor.py, file_validation.py |
| 1.3 | Action-to-container duplication | DRY | MEDIUM | flow_record.py |
| 5.1 | path_for() side effect | Purity | MEDIUM | file_accessor.py |
| 1.5 | Duplicate test fixtures | DRY | MEDIUM | tests/ |
| 1.6 | Frontend decoder duplication | DRY | MEDIUM | records.gleam |
| 6.3 | Missing role in example | Architecture | LOW | air_volume.json |
| 3.1 | No-op on_created() | YAGNI | LOW | flow_record.py |
| 2.1 | Defensive enum check | KISS | LOW | engine.py |
| 2.2 | Unnecessary factory | KISS | LOW | file_accessor.py |

---

## Proposed Refactoring Order

1. **Quick wins** (LOW risk, HIGH impact):
   - Extract `RECORD_EAGER_LOAD` constant in record_repository.py
   - Extract `_add_action()` helper in flow_record.py
   - Add `ensure_file_definition()` in file_schema.py, use in both accessor and validator
   - Fix `air_volume.json` missing role
   - Move duplicate test fixtures to conftest.py

2. **Medium effort** (MEDIUM risk):
   - Extract `_dispatch_flows()` in engine.py (consolidate 3 handlers)
   - Add public API to RecordFileAccessor, refactor file_checksums.py to use it
   - Add deletion detection to `checksums_changed()`
   - Parallelize `compute_checksums()` with `asyncio.gather()`
   - Rename `path_for()` -> `ensure_path_for()`

3. **Larger refactors** (consider separately):
   - Replace O(n) loop in `bulk_update_status()` with single UPDATE statement
   - Extract frontend common decoder logic in records.gleam
   - Clean up unused Gleam Record fields

## Verification

- `make format && make lint` -- must pass
- `make typecheck` -- must pass
- `make test` -- all existing tests must pass
- `make test-integration` -- integration tests must pass
- Manually test `POST /records/{id}/check-files` endpoint if available
