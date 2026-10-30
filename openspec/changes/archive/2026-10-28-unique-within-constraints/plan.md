# RecordType `unique_by` Cardinality Constraints — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `unique_per_user` boolean with a `unique_by` partition set over `{user, parent}` (applied within the record's own level context), add a `{parent_id}` file placeholder, add a config-load output-path-uniqueness validator, and make `parent_record_id` cascade on delete — so a downstream per-parent constraint becomes a config edit plus a startup fail-fast. Unify all enforcement points (create / inherit / assign / claim / claimable-pool SQL) behind the partition set, enable SQLite FK enforcement, and keep every legacy input path (RecordDef kwarg, TOML documents, API bodies) working via translation.

**Architecture:** `unique_by` is `frozenset[Literal["user","parent"]] | None` (default `{user,parent}`, `None` = no uniqueness) stored as a nullable canonical JSON column; it is the uniqueness key (implicit cap 1 per partition-combo, always scoped to the record's level) and is orthogonal to `max_records` (the unchanged per-level quota). One-record-per-level is spelled `max_records=1` + `unique_by=None`. The same value drives a config-load validator requiring every OUTPUT pattern to discriminate the dimensions on which coexisting records can differ. `unique_per_user` survives only as a deprecated compat input, translated by `mode="before"` validators on `RecordTypeCreate`/`RecordTypeOptional` (covering TOML documents and API bodies) and by the `RecordDef` kwarg. Enforcement follows the **bound-tuple rule**: the check runs only when every selected partition is bound (`parent` always — `None` is a value; `user` only when `user_id` is set), so unassigned claiming pools are permitted and the user axis is closed at claim/assign time. `unique_by = false` is the TOML spelling of `None`. SQLite connections gain `PRAGMA foreign_keys=ON` so the FK cascade is engine-independent.

**Tech Stack:** Python 3.12, SQLModel/SQLAlchemy (async), Pydantic v2, FastAPI, `uv`, pytest.

## Global Constraints

- All Python tooling via `uv run` / the Makefile; `make check` = format + lint + typecheck.
- New/changed columns on existing tables MUST declare a dialect-aware `server_default` matching the model default — enforced by `tests/migration/test_schema_integrity.py`.
- Repositories raise `clarinet.exceptions.domain`; API 409 codes preserved: `UNIQUE_PER_USER` (`RecordUniquePerUserError`), `RECORD_LIMIT_REACHED` (`RecordLimitReachedError`).
- `from __future__ import annotations` is forbidden in `table=True` model files.
- Docs edited in `.claude/rules/*.md` MUST be re-copied to the byte-identical `clarinet/docs/*` (golden-guard test).
- Framework scope only: NO alembic migration authored here (alembic is project-level); NO `clarinet_nir_liver` edits.
- `unique_per_user` must never be silently ignored on any input path — translation happens at the model boundary (`mode="before"`), and the TOML exporter never writes the legacy key.
- Logger: `from clarinet.utils.logger import logger`; f-strings in log calls.

---

## Task 1: `unique_by` canonicalization primitive

**Files:**
- Create: `clarinet/models/uniqueness.py`
- Test: `tests/test_uniqueness_partitions.py`

**Interfaces:**
- Produces: `UNIQUE_BY_PARTITIONS: frozenset[str] = frozenset({"user","parent"})`; `DEFAULT_UNIQUE_BY: frozenset[str] = frozenset({"user","parent"})`; `canonical_unique_by(value) -> frozenset[str] | None` — `None` → `None` (off); `False` → `None` (the TOML spelling of off); `True` → `ValueError` naming `{"user"}` and omission; an iterable → validated `frozenset` (unknown token → `ValueError`); an **empty** iterable → `ValueError` naming `None` and `max_records=1`. Plus `legacy_unique_per_user(value: bool) -> frozenset[str] | None` (`True→{"user"}`, `False→None`) — the single mapping shared by `RecordDef` and the model before-validators.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_uniqueness_partitions.py
import pytest
from clarinet.models.uniqueness import (
    DEFAULT_UNIQUE_BY,
    canonical_unique_by,
    legacy_unique_per_user,
)

def test_order_and_dupe_insensitive():
    assert canonical_unique_by(["user", "parent", "user"]) == frozenset({"parent", "user"})

def test_none_is_off():
    assert canonical_unique_by(None) is None

def test_false_is_toml_off():
    assert canonical_unique_by(False) is None

def test_true_rejected_with_teaching_message():
    with pytest.raises(ValueError, match="user"):
        canonical_unique_by(True)

def test_default_is_user_parent():
    assert DEFAULT_UNIQUE_BY == frozenset({"user", "parent"})

def test_empty_rejected_with_teaching_message():
    with pytest.raises(ValueError, match="None.*max_records=1|max_records=1.*None"):
        canonical_unique_by(set())

def test_unknown_token_rejected():
    with pytest.raises(ValueError, match="level"):
        canonical_unique_by({"level"})

def test_legacy_mapping():
    assert legacy_unique_per_user(True) == frozenset({"user"})
    assert legacy_unique_per_user(False) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_uniqueness_partitions.py -q`
Expected: FAIL — `ModuleNotFoundError: clarinet.models.uniqueness`

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/models/uniqueness.py
"""Canonicalization for the RecordType ``unique_by`` uniqueness partitions."""
from collections.abc import Iterable

UNIQUE_BY_PARTITIONS: frozenset[str] = frozenset({"user", "parent"})
DEFAULT_UNIQUE_BY: frozenset[str] = frozenset({"user", "parent"})


def canonical_unique_by(value: Iterable[str] | bool | None) -> frozenset[str] | None:
    """``None``/``False`` → no uniqueness. A set/list → validated frozenset.

    ``False`` is the TOML spelling of off (TOML has no null). ``True`` and an
    empty iterable are rejected with teaching messages.
    """
    if value is None or value is False:
        return None
    if value is True:
        raise ValueError(
            "unique_by=true is not a partition set — use {'user'} for the legacy "
            "unique_per_user behavior, or omit the field for the default"
        )
    parts = frozenset(value)
    if not parts:
        raise ValueError(
            "unique_by cannot be empty — use None for no uniqueness, "
            "or max_records=1 for one-per-level"
        )
    unknown = parts - UNIQUE_BY_PARTITIONS
    if unknown:
        raise ValueError(f"unknown unique_by partition: {sorted(unknown)}")
    return parts


def legacy_unique_per_user(value: bool) -> frozenset[str] | None:
    """Deprecated ``unique_per_user`` mapping: ``True→{"user"}``, ``False→None``."""
    return frozenset({"user"}) if value else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_uniqueness_partitions.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add clarinet/models/uniqueness.py tests/test_uniqueness_partitions.py
git commit -m "feat(models): unique_by partition canonicalization primitive"
```

---

## Task 2: `{parent_id}` file-pattern placeholder

**Files:**
- Modify: `clarinet/files/_patterns.py:94-103` (the `fields_from` return dict)
- Test: `tests/test_file_patterns.py` (add a case; create if absent)

**Interfaces:**
- Consumes: `RecordRead.parent_record_id`.
- Produces: `fields_from(...)["parent_id"]` == the FK column value; renderable as `{parent_id}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_file_patterns.py
from clarinet.files._patterns import fields_from

def test_parent_id_from_fk_is_load_independent(make_record_read):
    rec = make_record_read(parent_record_id=4711)
    assert fields_from(rec, parent=None)["parent_id"] == 4711
    assert fields_from(rec, parent=make_record_read(id=4711))["parent_id"] == 4711

def test_parent_id_none_stays_none(make_record_read):
    # Lenient template rendering turns None into "" (strict raises) — pinned in spec.
    assert fields_from(make_record_read(parent_record_id=None), parent=None)["parent_id"] is None
```

(If no `make_record_read` fixture exists, build a minimal `RecordRead` inline with a `record_type` carrying `.name` and `.level`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_file_patterns.py::test_parent_id_from_fk_is_load_independent -q`
Expected: FAIL — `KeyError: 'parent_id'`

- [ ] **Step 3: Write minimal implementation**

In `clarinet/files/_patterns.py`, add one key to the `fields_from` return dict (after `"id": record.id`):

```python
        "id": record.id,
        "parent_id": record.parent_record_id,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_file_patterns.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clarinet/files/_patterns.py tests/test_file_patterns.py
git commit -m "feat(files): {parent_id} placeholder resolved from FK column"
```

---

## Task 3: Model column — `unique_by` replaces `unique_per_user`

**Files:**
- Modify: `clarinet/models/record_type.py:81-84` (field), `:194-206` (`_validate_shared_editing`), `:363` (`RecordTypeOptional`), before-validators on `RecordTypeCreate`/`RecordTypeOptional`
- Modify: `clarinet/models/record.py:202-206` (FK `ondelete`)
- Modify: `clarinet/models/file_schema.py` (`allow_path_collision` on `RecordTypeFileLink` + `FileDefinitionRead`)
- Modify: `clarinet/utils/db_manager.py:29` (`_pydantic_json_serializer` learns set/frozenset)
- Test: `tests/test_record_type_model.py`

**Interfaces:**
- Produces: `RecordTypeBase.unique_by: frozenset[str] | None` (nullable JSON column; `null`=None/off, else canonical sorted list; `server_default '["parent", "user"]'`). `unique_per_user` column REMOVED.
- Consumes: `canonical_unique_by`, `DEFAULT_UNIQUE_BY` (Task 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_record_type_model.py
import pytest
from pydantic import ValidationError
from clarinet.models.record_type import RecordTypeCreate

def test_unique_by_default_and_canonical():
    assert RecordTypeCreate(name="x", unique_by=["user", "parent"]).unique_by == frozenset({"parent", "user"})
    assert RecordTypeCreate(name="y").unique_by == frozenset({"user", "parent"})
    assert RecordTypeCreate(name="z", unique_by=None).unique_by is None

def test_empty_unique_by_rejected():
    with pytest.raises(ValidationError, match="max_records=1"):
        RecordTypeCreate(name="e", unique_by=set())

def test_shared_editing_requires_no_user_partition():
    with pytest.raises(ValidationError, match="shared_editing"):
        RecordTypeCreate(name="s", shared_editing=True, unique_by={"user"})

def test_legacy_key_translated_on_create():
    with pytest.warns(DeprecationWarning):
        rt = RecordTypeCreate(name="l", unique_per_user=False)
    assert rt.unique_by is None  # NOT silently ignored into the default

def test_legacy_key_translated_on_patch():
    from clarinet.models.record_type import RecordTypeOptional
    with pytest.warns(DeprecationWarning):
        p = RecordTypeOptional(unique_per_user=True)
    assert p.unique_by == frozenset({"user"}) and "unique_by" in p.model_fields_set
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_record_type_model.py -q`
Expected: FAIL — `unique_by` unknown field / validator absent.

- [ ] **Step 3: Write minimal implementation**

Replace the `unique_per_user` field (`record_type.py:81-84`) with a nullable JSON column plus a canonicalizing validator and a sorting serializer, and re-point `_validate_shared_editing`:

```python
from clarinet.models.uniqueness import DEFAULT_UNIQUE_BY, canonical_unique_by
from clarinet.types import PortableJSON
from sqlalchemy import Column
from pydantic import field_validator, field_serializer

    unique_by: frozenset[str] | None = Field(
        default_factory=lambda: frozenset(DEFAULT_UNIQUE_BY),
        sa_column=Column(PortableJSON, nullable=True, server_default='["parent", "user"]'),
    )

    @field_validator("unique_by", mode="before")
    @classmethod
    def _canonical_unique_by(cls, v: object) -> frozenset[str] | None:
        return canonical_unique_by(v)  # type: ignore[arg-type]

    @field_serializer("unique_by")
    def _ser_unique_by(self, v: frozenset[str] | None) -> list[str] | None:
        return None if v is None else sorted(v)
```

Update `_validate_shared_editing`:

```python
        if self.shared_editing and "user" in (self.unique_by or frozenset()):
            raise ValueError("shared_editing requires 'user' not in unique_by")
```

In `RecordTypeOptional` (`:363`) replace `unique_per_user: bool | None` with:

```python
    unique_by: frozenset[str] | None = Field(default=None)
```

> Note: `None` is a real value ("off"), so PATCH presence must be detected via
> `model_fields_set`, not a `None` check. Verify the `/types/{name}` PATCH path and the
> reconciler use `model_fields_set` (they already do for None-default fields — issue #389).

Add the legacy translation to BOTH `RecordTypeCreate` and `RecordTypeOptional` (TOML
documents — `bootstrap.py:388` — and API bodies reach these models as dicts; the key must
be translated, never silently ignored):

```python
    @model_validator(mode="before")
    @classmethod
    def _translate_legacy_unique_per_user(cls, data: Any) -> Any:
        if isinstance(data, dict) and "unique_per_user" in data:
            legacy = data.pop("unique_per_user")
            warnings.warn(
                "unique_per_user is deprecated; use unique_by",
                DeprecationWarning, stacklevel=2,
            )
            if legacy is not None and "unique_by" not in data:
                data["unique_by"] = legacy_unique_per_user(legacy)
        return data
```

Remove dangling `unique_per_user` references; keep `sql_expression` import if still used.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_record_type_model.py -q`
Expected: PASS

- [ ] **Step 5: Extend the server-default metadata guard**

In `tests/migration/test_schema_integrity.py`, ensure the `unique_by` column is examined and its `server_default` (`["parent", "user"]`) matches the model default. Run:

Run: `uv run pytest tests/migration/test_schema_integrity.py -q`
Expected: PASS

- [ ] **Step 6: `allow_path_collision` on the file link**

Add to `RecordTypeFileLink` (`clarinet/models/file_schema.py`):

```python
    allow_path_collision: bool = Field(
        default=False,
        sa_column_kwargs={"server_default": sql_expression.false()},
    )
```

Add the field to `FileDefinitionRead` (default `False`) and thread it through
`RecordType.file_registry`. Extend the server-default guard to cover it.

- [ ] **Step 7: Engine JSON serializer accepts frozensets**

In `_pydantic_json_serializer` (`clarinet/utils/db_manager.py:29`) extend `default()` to
serialize `set`/`frozenset` as `sorted(...)` lists — a frozenset reaching a JSON-column
flush must never raise.

- [ ] **Step 8: Change the FK ondelete**

`clarinet/models/record.py:205`: `ondelete="SET NULL"` → `ondelete="CASCADE"`.

- [ ] **Step 9: Commit**

```bash
git add clarinet/models/record_type.py clarinet/models/record.py clarinet/models/file_schema.py clarinet/utils/db_manager.py tests/test_record_type_model.py tests/migration/test_schema_integrity.py
git commit -m "feat(models): unique_by column, legacy translation, link opt-out, ON DELETE CASCADE"
```

---

## Task 4: Config primitive — `unique_by` + `unique_per_user` compat kwarg

**Files:**
- Modify: `clarinet/config/primitives.py:153` (field + `__init__` around `:162`), `FileRef` (`allow_path_collision` param) + `fileref_to_file_definition` (`:181`)
- Modify: `clarinet/config/python_loader.py:209` (`_to_record_type_create` forwarding)
- Test: `tests/test_config_primitives.py`

**Interfaces:**
- Consumes: `canonical_unique_by`, `DEFAULT_UNIQUE_BY`, `legacy_unique_per_user` (Task 1).
- Produces: `RecordDef.unique_by: frozenset|None`; `RecordDef(unique_per_user=True|False)` translates + warns; explicit `unique_by` wins; `FileRef(..., allow_path_collision=True)` reaches `FileDefinitionRead`; `_to_record_type_create` forwards `unique_by` (never the legacy boolean).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config_primitives.py
import pytest
from clarinet.flow import RecordDef

def test_unique_per_user_true_maps_to_user():
    with pytest.warns(DeprecationWarning):
        rd = RecordDef(name="a", unique_per_user=True)
    assert rd.unique_by == frozenset({"user"})

def test_unique_per_user_false_maps_to_none():
    with pytest.warns(DeprecationWarning):
        rd = RecordDef(name="b", unique_per_user=False)
    assert rd.unique_by is None

def test_default_is_user_parent():
    assert RecordDef(name="c").unique_by == frozenset({"user", "parent"})

def test_explicit_unique_by_wins_over_flag():
    with pytest.warns(DeprecationWarning):
        rd = RecordDef(name="d", unique_by={"parent"}, unique_per_user=True)
    assert rd.unique_by == frozenset({"parent"})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config_primitives.py -q`
Expected: FAIL — `unique_per_user` unexpected / no translation.

- [ ] **Step 3: Write minimal implementation**

Replace `unique_per_user: bool = True` (`:153`) with:

```python
    unique_by: frozenset[str] | None = Field(default_factory=lambda: frozenset(DEFAULT_UNIQUE_BY))
```

In `RecordDef.__init__`, translate the legacy kwarg before `super().__init__`:

```python
    def __init__(self, *, role: str | None = None, unique_per_user: bool | None = None, **kwargs):
        if role is not None and "role_name" not in kwargs:
            kwargs["role_name"] = role
        if unique_per_user is not None:
            warnings.warn(
                "unique_per_user is deprecated; use unique_by",
                DeprecationWarning, stacklevel=2,
            )
            if "unique_by" not in kwargs:  # explicit unique_by wins
                kwargs["unique_by"] = legacy_unique_per_user(unique_per_user)
        super().__init__(**kwargs)
```

Add a `@field_validator("unique_by", mode="before")` calling `canonical_unique_by`. Add `import warnings` and the `DEFAULT_UNIQUE_BY`/`canonical_unique_by`/`legacy_unique_per_user` imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_config_primitives.py -q`
Expected: PASS

- [ ] **Step 5: `FileRef.allow_path_collision`**

Add `allow_path_collision: bool = False` to `FileRef`; carry it in
`fileref_to_file_definition` (`primitives.py:181`) into the produced
`FileDefinitionRead`. Test: the flag survives the conversion.

- [ ] **Step 6: Loader forwarding**

In `_to_record_type_create` (`clarinet/config/python_loader.py:209`) replace the
`unique_per_user` branch with:

```python
    if "unique_by" in rt_def.model_fields_set:
        kwargs["unique_by"] = rt_def.unique_by
```

Test: `RecordDef(name="p", unique_by={"parent"})` produces a `RecordTypeCreate` with
`unique_by == frozenset({"parent"})`; an unspecified `unique_by` stays out of
`model_fields_set` (reconciler heal semantics preserved).

- [ ] **Step 7: Commit**

```bash
git add clarinet/config/primitives.py clarinet/config/python_loader.py tests/test_config_primitives.py
git commit -m "feat(config): RecordDef.unique_by, FileRef opt-out, loader forwarding"
```

---

## Task 5: Enforcement surface — partition counting, service call sites, pool filter

**Files:**
- Modify: `clarinet/repositories/record_repository.py:1263-1296` (`ensure_unique_per_user` → `ensure_unique_by`), `:1253-1261` (call in `check_constraints`), `:326-361` (`_unique_per_user_violation_filter`) + its five call sites `:545`, `:596`, `:1425`, `:1679`, `:1822`
- Modify: `clarinet/services/record_service.py:219` (inheritance re-check), `:1137-1166` (`_check_unique_per_user`; callers `:331`, `:368`, `:464`)
- Test: `tests/integration/test_unique_by.py`

**Interfaces:**
- Consumes: `record_type.unique_by`, `parent_record_id` (already a `check_constraints` param, `:1205`).
- Produces: `ensure_unique_by(record_type, *, user_id, parent_record_id, patient_id, study_uid, series_uid)` — 409 `UNIQUE_PER_USER` iff an existing record of the type in the same level context matches on every selected partition, run only on a fully bound tuple (skipped when `"user"` is selected and `user_id is None`); `_unique_by_violation_filter(user_id, user_only, user_parent)` + `_partitioned_unique_type_names(session)`; `max_records` counting untouched.

- [ ] **Step 1: Write the failing tests** (integration, real async session/fixtures)

```python
# tests/integration/test_unique_by.py
async def test_parent_partition_blocks_same_parent(client, seed_parented_type):
    r1 = await create_record(client, "child", parent_record_id=1)   # 201
    r2 = await create_record(client, "child", parent_record_id=1)   # 409 UNIQUE_PER_USER
    assert r2.status_code == 409 and r2.json()["code"] == "UNIQUE_PER_USER"
    r3 = await create_record(client, "child", parent_record_id=2)   # 201
    assert r3.status_code == 201

async def test_parentless_default_collapses_to_per_user(client, seed_user_type):
    ...  # unique_by default; same assigned user -> 409; another user -> 201

async def test_none_only_max_records_binds(client, seed_none_type):
    ...  # unique_by=None, max_records=3 -> 3 allowed, 4th 409 RECORD_LIMIT_REACHED

async def test_quota_and_uniqueness_together(client, seed_first_check):
    ...  # max_records=2 per study + unique_by={"user"}: 2 users ok, dup user 409, 3rd 409 limit

async def test_unassigned_pool_coexists(client, seed_user_type):
    ...  # bound-tuple rule: unique_by={"user"}, two unassigned creations -> both 201

async def test_parent_only_dedupes_unassigned(client, seed_parent_only_type):
    ...  # unique_by={"parent"}: two unassigned same-parent -> second 409

async def test_unassigned_does_not_block_assigned(client, seed_parented_type):
    ...  # {"user","parent"}: unassigned (None,P) exists; user U creates (U,P) -> 201

async def test_claim_enforces_partitions(client, seed_parented_type):
    ...  # U holds (U,P): claim of unassigned (None,P) -> 409; of (None,Q) -> 200

async def test_claimable_listing_matches_claim(client, seed_parented_type):
    ...  # pool for U excludes the parent-P record, includes parent-Q; a fresh user sees both
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/integration/test_unique_by.py -q`
Expected: FAIL — parent partition not counted; claim/pool unaware of partitions.

- [ ] **Step 3: `ensure_unique_by` (bound-tuple rule)**

Rewrite `ensure_unique_per_user` (`:1263`) as `ensure_unique_by`; signature gains `user_id: UUID | None` and `parent_record_id: int | None`:

```python
        parts = record_type.unique_by
        if parts is None:
            return
        if "user" in parts and user_id is None:
            # Bound-tuple rule: an unassigned record's user axis is not yet
            # evaluable — pools are permitted; the invariant is enforced at
            # claim/assign time via this same method with user_id set.
            return
        query = select(func.count(col(Record.id))).where(
            Record.record_type_name == record_type.name,
        )
        match record_type.level:
            case DicomQueryLevel.PATIENT:
                query = query.where(Record.patient_id == patient_id)
            case DicomQueryLevel.STUDY:
                query = query.where(Record.study_uid == study_uid)
            case DicomQueryLevel.SERIES:
                query = query.where(Record.series_uid == series_uid)
        if "user" in parts:
            query = query.where(col(Record.user_id) == user_id)
        if "parent" in parts:
            query = query.where(
                col(Record.parent_record_id).is_(None)
                if parent_record_id is None
                else Record.parent_record_id == parent_record_id
            )
        if (await self.session.execute(query)).scalar_one() > 0:
            raise RecordUniquePerUserError(
                f"A record of type '{record_type.name}' already exists for "
                f"unique_by={sorted(parts)} in this {record_type.level.lower()} context"
            )
```

Update the `check_constraints` call (`:1254-1261`): drop the `user_id is not None` gate, pass `parent_record_id`. Delete `count_user_records_for_context` if unused after Step 4.

- [ ] **Step 4: Service call sites**

- `record_service.py:219` (inheritance re-check): `ensure_unique_by(record_type, user_id=parent.user_id, parent_record_id=record.parent_record_id, patient_id=record.patient_id, study_uid=record.study_uid, series_uid=record.series_uid)`.
- `_check_unique_per_user` (`:1137`): replace the body with a call to `ensure_unique_by(record.record_type, user_id=user_id, parent_record_id=record.parent_record_id, ...)` — the `if not record_type.unique_per_user` gate disappears (the method self-gates on `unique_by`). Callers `:331/:368/:464` stay as-is.

- [ ] **Step 5: Pool filter over prefetched type names**

Replace `_unique_per_user_violation_filter` (`:326`) — it references the dropped column in SQL and misses the parent partition:

```python
async def _partitioned_unique_type_names(session) -> tuple[set[str], set[str]]:
    """Names of types with 'user' in unique_by, split by whether 'parent' is too."""
    rows = (await session.execute(select(RecordType.name, RecordType.unique_by))).all()
    user_only, user_parent = set(), set()
    for name, ub in rows:
        parts = set(ub or [])
        if "user" in parts:
            (user_parent if "parent" in parts else user_only).add(name)
    return user_only, user_parent


def _unique_by_violation_filter(user_id, user_only: set[str], user_parent: set[str]):
    def _ctx_exists(match_parent: bool):
        inner_r = aliased(Record, flat=True)
        conds = [
            col(inner_r.user_id) == user_id,
            inner_r.record_type_name == Record.record_type_name,
            or_(
                and_(col(RecordType.level) == "SERIES", inner_r.series_uid == Record.series_uid),
                and_(col(RecordType.level) == "STUDY", inner_r.study_uid == Record.study_uid),
                and_(col(RecordType.level) == "PATIENT", inner_r.patient_id == Record.patient_id),
            ),
        ]
        if match_parent:  # NULL-safe match against the candidate row's parent
            conds.append(or_(
                and_(col(inner_r.parent_record_id).is_(None), col(Record.parent_record_id).is_(None)),
                inner_r.parent_record_id == Record.parent_record_id,
            ))
        return exists(select(literal(1)).select_from(inner_r).where(*conds).correlate(Record, RecordType))

    return ~and_(
        col(Record.user_id).is_(None),
        or_(
            and_(col(Record.record_type_name).in_(user_only), _ctx_exists(False)),
            and_(col(Record.record_type_name).in_(user_parent), _ctx_exists(True)),
        ),
    )
```

Callers (`:545`, `:596`, `:1425`, `:1679`, `:1822`) prefetch the two sets once per query and pass them in. `in_(set())` on an empty set renders a false condition — safe.

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/integration/test_unique_by.py -q`
Expected: PASS

- [ ] **Step 7: Regression — existing constraint/claim tests**

Run: `uv run ./scripts/run_tests.sh -k "unique or constraint or claim or parent_child or available" -q`
Expected: PASS (adjust tests that set `unique_per_user` to use `unique_by`).

- [ ] **Step 8: Commit**

```bash
git add clarinet/repositories/record_repository.py clarinet/services/record_service.py tests/integration/test_unique_by.py
git commit -m "feat(repo): partition-aware unique_by enforcement across create/claim/pool"
```

---

## Task 6: Output-path uniqueness validator

**Files:**
- Create: `clarinet/config/path_uniqueness.py`
- Modify: `clarinet/models/record_type.py` (call from the `RecordTypeCreate` model validator), `clarinet/config/python_loader.py` + the TOML load path, `clarinet/services/record_type_service.py:95` (PATCH validates the merged result)
- Test: `tests/test_path_uniqueness.py`

**Interfaces:**
- Produces: `validate_output_path_uniqueness(rt: RecordTypeCreate) -> None` — raises `RecordConstraintViolationError` (config context: `ConfigLoadError`) naming the RecordType + FileDef. Reads `unique_by`, `parent_required`, `max_records`, `level`, and `file_registry` (name/pattern/multiple/level/role/`allow_path_collision` — the per-file opt-out from Tasks 3/4).
- Consumes: `PLACEHOLDER_REGEX` (`clarinet/files/_patterns.py:35`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_path_uniqueness.py
import pytest
from clarinet.config.path_uniqueness import validate_output_path_uniqueness
from clarinet.exceptions.domain import RecordConstraintViolationError
from tests.factories import make_record_type_create  # OUTPUT FileDef helper

def test_parent_partition_without_discriminator_rejected():
    rt = make_record_type_create(name="second-review", level="SERIES", parent_required=True,
        unique_by={"parent"}, output_pattern="review_{user_id}.seg.nrrd")
    with pytest.raises(RecordConstraintViolationError, match="second-review"):
        validate_output_path_uniqueness(rt)

def test_parent_partition_with_parent_id_passes():
    rt = make_record_type_create(name="second-review", level="SERIES", parent_required=True,
        unique_by={"parent"}, output_pattern="review_{parent_id}.seg.nrrd")
    validate_output_path_uniqueness(rt)

def test_id_fast_pass():
    rt = make_record_type_create(name="anyid", level="SERIES", parent_required=True,
        unique_by={"parent"}, output_pattern="out_{id}.nrrd")
    validate_output_path_uniqueness(rt)

def test_none_with_multi_quota_requires_id():
    rt = make_record_type_create(name="m", level="SERIES", unique_by=None, max_records=5,
        output_pattern="out_{user_id}.nrrd")
    with pytest.raises(RecordConstraintViolationError):
        validate_output_path_uniqueness(rt)

def test_origin_type_alone_rejected():
    rt = make_record_type_create(name="ot", level="SERIES", parent_required=True,
        unique_by={"parent"}, output_pattern="seg_{origin_type}_{user_id}.nrrd")
    with pytest.raises(RecordConstraintViolationError):
        validate_output_path_uniqueness(rt)  # two same-type parents render one filename

def test_multiple_true_exempt():
    rt = make_record_type_create(name="coll", level="SERIES", parent_required=True,
        unique_by={"parent"}, output_pattern="seg_{origin_type}.nrrd", multiple=True)
    validate_output_path_uniqueness(rt)

def test_per_file_optout_suppresses():
    rt = make_record_type_create(name="rep", level="SERIES", unique_by=None, max_records=4,
        output_pattern="report_{data.timepoint}.pdf", file_allow_path_collision=True)
    validate_output_path_uniqueness(rt)

def test_optout_does_not_cover_siblings():
    rt = make_record_type_create_two_outputs(name="rep2", level="SERIES", unique_by=None,
        max_records=4,
        outputs=[("report_{data.timepoint}.pdf", True), ("summary.txt", False)])
    with pytest.raises(RecordConstraintViolationError, match="summary"):
        validate_output_path_uniqueness(rt)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_path_uniqueness.py -q`
Expected: FAIL — module missing.

- [ ] **Step 3: Write minimal implementation**

```python
# clarinet/config/path_uniqueness.py
"""Config-load validation: OUTPUT patterns must discriminate coexisting records."""
from clarinet.files._patterns import PLACEHOLDER_REGEX
from clarinet.exceptions.domain import RecordConstraintViolationError

_LEVEL_UID = {"PATIENT": None, "STUDY": "study_uid", "SERIES": "series_uid"}
_LEVEL_ORDER = {"PATIENT": 0, "STUDY": 1, "SERIES": 2}


def _placeholders(pattern: str) -> set[str]:
    return set(PLACEHOLDER_REGEX.findall(pattern))


def validate_output_path_uniqueness(rt) -> None:
    parts = rt.unique_by  # frozenset | None
    for fd in (rt.file_registry or []):
        if fd.role != "OUTPUT" or getattr(fd, "multiple", False):
            continue
        if getattr(fd, "allow_path_collision", False):
            continue  # per-file opt-out: author-guaranteed unique; siblings stay checked
        ph = _placeholders(fd.pattern)
        if "id" in ph:
            continue  # record.id is globally unique -> always safe
        missing: list[str] = []
        if parts and "user" in parts and "user_id" not in ph:
            missing.append("user -> {user_id}")
        if parts and "parent" in parts and rt.parent_required and "parent_id" not in ph:
            # {origin_type} distinguishes parent *types*, not two same-type
            # parents — only the per-file opt-out excuses relying on it.
            missing.append("parent -> {parent_id}")
        # level context is always part of the uniqueness scope:
        fd_level = fd.level or rt.level
        if _LEVEL_ORDER[fd_level] < _LEVEL_ORDER[rt.level]:
            uid = _LEVEL_UID[rt.level]
            if uid and uid not in ph:
                missing.append(f"level -> {{{uid}}}")
        # no uniqueness + a multi-record quota -> nothing distinguishes rows but {id}:
        if parts is None and (rt.max_records is None or rt.max_records > 1):
            missing.append("no-uniqueness -> {id}")
        if missing:
            raise RecordConstraintViolationError(
                f"RecordType '{rt.name}' OUTPUT file '{fd.name}' pattern "
                f"'{fd.pattern}' cannot distinguish coexisting records: missing {missing}"
            )
```

The `allow_path_collision` flag lives on the file binding (Task 3 Step 6: `RecordTypeFileLink` column + `FileDefinitionRead`; Task 4 Step 5: `FileRef` param) — no RecordType-level field.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_path_uniqueness.py -q`
Expected: PASS

- [ ] **Step 5: Wire into config load + model validator**

- In `RecordTypeCreate`'s model validator (Task 3), call `validate_output_path_uniqueness(self)` after canonicalization (import lazily to avoid a cycle).
- In `clarinet/config/python_loader.py` and the TOML branch of `bootstrap.reconcile_config`, the `RecordTypeCreate` construction runs the validator; ensure the raised `RecordConstraintViolationError` is wrapped as `ConfigLoadError`. Add a loader test asserting a bad OUTPUT pattern aborts startup naming the type+file.

Run: `uv run pytest tests/integration/test_config_python_mode.py tests/integration/test_config_reconciler.py -q`
Expected: PASS

- [ ] **Step 6: PATCH validates the merged result**

In `RecordTypeService.update` (`record_type_service.py:95`), after applying the
`RecordTypeOptional` fields onto the existing row, build the effective
`RecordTypeCreate` (scalars + resulting `file_registry`) and validate it (path predicate
+ `_validate_shared_editing` run via model validation). A violating mutation of
`unique_by` / `max_records` / `parent_required` / `file_registry` must be rejected at
PATCH time — in TOML mode it would otherwise export to disk and fail the next startup.
Test: PATCH flipping `unique_by` to a combination the OUTPUT pattern cannot discriminate
→ 409/422, DB unchanged.

- [ ] **Step 7: Commit**

```bash
git add clarinet/config/path_uniqueness.py clarinet/models/record_type.py clarinet/config/primitives.py clarinet/config/python_loader.py clarinet/services/record_type_service.py tests/test_path_uniqueness.py
git commit -m "feat(config): output-path uniqueness validator + per-file opt-out + PATCH guard"
```

---

## Task 7: Config reconciler canonical comparison

**Files:**
- Modify: `clarinet/config/reconciler.py` (field comparison for `unique_by`)
- Test: `tests/integration/test_config_reconciler.py`

**Interfaces:**
- Consumes: canonical `unique_by` (frozenset|None) from both config and DB row.
- Produces: reorder = no-op; `None`↔set is a real change; drift heals toward config.

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_config_reconciler.py
async def test_unique_by_reorder_is_unchanged(session):
    ...  # DB ["parent","user"] (raw list), config {"user","parent"} -> no update
async def test_unique_by_none_vs_set_is_a_change(session):
    ...  # DB None, config {"user"} -> update
async def test_unique_by_explicit_none_overwrites_default(session):
    ...  # DB ["parent","user"] (the default), config explicitly None -> update to NULL
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/integration/test_config_reconciler.py -k unique_by -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

DB rows skip validation (`table=True`) — `db_record_type.unique_by` is the **raw JSON
list**, so canonicalize BOTH sides via `canonical_unique_by` before comparing (a naive
`==` on list vs frozenset reports a perpetual diff every startup). An explicit
`unique_by=None` (present in `config.model_fields_set`) is a real target value: it must
bypass the `config_val is None and db_val == default` skip at `reconciler.py:133` and
write `NULL` over a default-valued row. Confirm the non-None-default heals when unset
(issue #389 class); `None` is a set value, not "unset".

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/integration/test_config_reconciler.py -k unique_by -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clarinet/config/reconciler.py tests/integration/test_config_reconciler.py
git commit -m "fix(config): reconcile unique_by canonically (raw-list rows, explicit None honored)"
```

---

## Task 8: TOML exporter & legacy TOML compat

**Files:**
- Modify: `clarinet/config/toml_exporter.py:27` (field list)
- Test: exporter unit tests + a TOML-mode integration test with a legacy fixture

**Interfaces:**
- Produces: exported TOML carries `unique_by` (`false` for `None`, the canonical sorted list otherwise) and never `unique_per_user`; a legacy `.toml` with `unique_per_user` loads translated + warned (via Task 3's before-validator on `RecordTypeCreate`).

- [ ] **Step 1: Write the failing tests**

- Export a type with `unique_by=None` → the TOML body contains `unique_by = false`.
- Export a type with `unique_by={"user"}` → `unique_by = ["user"]`.
- A legacy `.toml` fixture carrying `unique_per_user = false` loads as `unique_by=None` with a `DeprecationWarning` (not silently flipped to the default).
- Round-trip: export an off-type → reload → still off.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest -k "toml and (export or unique_by)" -q`
Expected: FAIL — exporter still writes `unique_per_user`.

- [ ] **Step 3: Implement**

Replace `"unique_per_user"` in the exporter field list (`toml_exporter.py:27`) with `"unique_by"`; serialize `None → False` and a set → sorted list (TOML has no null; absence would heal to the default and silently re-enable uniqueness).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest -k "toml and (export or unique_by)" -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add clarinet/config/toml_exporter.py tests/
git commit -m "feat(config): TOML unique_by export (false = off) + legacy key compat"
```

---

## Task 9: SQLite FK enforcement

**Files:**
- Modify: `clarinet/utils/db_manager.py:92-96` (`_set_sqlite_pragmas`), the SQLite init path (lifespan / `create_db_and_tables_async`) for the audit
- Test: engine/DB manager tests + a cascade parity integration test

- [ ] **Step 1: Write the failing tests**

- A connection obtained from `DatabaseManager`'s engine reports `PRAGMA foreign_keys` == 1.
- A raw SQL `DELETE` of a parent record through the app engine cascades to children on SQLite (parity with PG).
- A seeded dangling row (`PRAGMA foreign_key_check` violation) → startup logs a WARNING per violation and completes.

Note: `tests/conftest.py:230` sets the pragma on its own fixture engines — these tests must exercise the app's `DatabaseManager` engine, not the fixture engine.

- [ ] **Step 2: Run to verify failure**

Expected: FAIL — `foreign_keys` is 0 on app-engine connections.

- [ ] **Step 3: Implement**

Add `cursor.execute("PRAGMA foreign_keys=ON")` to `_set_sqlite_pragmas` (`db_manager.py:92`, beside WAL/busy_timeout — per-connection, which the connect hook already guarantees). In the SQLite init path run `PRAGMA foreign_key_check` and `logger.warning(...)` each violating row; never abort.

- [ ] **Step 4: Run to verify pass**

Expected: PASS (incl. the existing suite — code paths already run FK-ON under PG and the test fixtures).

- [ ] **Step 5: Commit**

```bash
git add clarinet/utils/db_manager.py tests/
git commit -m "feat(db): enforce foreign keys on SQLite + startup FK audit"
```

---

## Task 10: Frontend `unique_by`

**Files:**
- Modify: `clarinet/frontend/src/api/models.gleam:81`, `:256` (record fields), `clarinet/frontend/src/api/records.gleam:109-110`, `:177`, `:561-562`, `:633` (decoders/encoders), `clarinet/frontend/src/pages/record_types/edit.gleam:143` (form schema), `:242` (PATCH body)

- [ ] **Step 1: Models + decoders** — replace `unique_per_user: Bool` with `unique_by: Option(List(String))` in both records; decode via `decode.optional_field("unique_by", ...)` (JSON `null` → `None` = off); encoders emit `unique_by`.

- [ ] **Step 2: Edit page** — replace the `unique_per_user` toggle in the embedded form schema with a `unique_by` select over the canonical spellings (default `["parent","user"]` / `["user"]` / `["parent"]` / off → `null`); the PATCH body sends `unique_by`.

- [ ] **Step 3: Verify** — Run: `make frontend-check` and the frontend tests.
Expected: PASS; `grep -rn "unique_per_user" clarinet/frontend/src # bash-ok` → no hits.

- [ ] **Step 4: Commit**

```bash
git add clarinet/frontend/src
git commit -m "feat(frontend): unique_by replaces unique_per_user in models and edit page"
```

---

## Task 11: Examples, docs, changelog

**Files:**
- Modify: `examples/demo/tasks/definitions/record_types.py`, `examples/project_template/plan/definitions/record_types.py`
- Modify: `.claude/rules/recordflow-dsl.md`, `.claude/rules/record-repo.md`, `.claude/rules/file-registry.md`, `clarinet/config/CLAUDE.md`, `clarinet/models/CLAUDE.md`; re-copy to `clarinet/docs/*`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Migrate example configs** — replace `unique_per_user=` with `unique_by=`; add one parent-scoped example (`unique_by={"parent"}` + `{parent_id}` OUTPUT pattern) in `examples/demo`. Run the demo config-load test to confirm they still load.

- [ ] **Step 2: Docs** — document: `unique_by` (partitions within implicit level scope; `None`=off; `False`=TOML off; `max_records=1`=one-per-level; empty rejected); the bound-tuple rule (unassigned pools permitted, user axis closed at claim/assign; `{"parent"}`-only types dedupe at creation regardless of assignment); `max_records` orthogonality; `{parent_id}` vs `{origin_type}` (exact vs opt-out-only, load-independence); the path-uniqueness fail-fast + per-FileRef `allow_path_collision`; the CASCADE audit trade + SQLite FK enforcement. Re-copy each edited `.claude/rules/*.md` to its `clarinet/docs/*` twin.

- [ ] **Step 3: Verify golden-guard** — Run: `uv run pytest -k "golden or docs_sync" -q`
Expected: PASS

- [ ] **Step 4: CHANGELOG** — entry covering `unique_per_user → unique_by` (+ mapping `True→{user}`, `False→None`), the path-uniqueness validator, `parent_record_id ON DELETE CASCADE` (FK-cascade deletes emit no `RecordEvent`), and SQLite FK enforcement (pragma + startup audit). Add the downstream follow-up note (alembic migration incl. `recordtypefilelink.allow_path_collision` + config edits + backfill 25 reviews).

- [ ] **Step 5: Commit**

```bash
git add examples .claude/rules clarinet/docs clarinet/config/CLAUDE.md clarinet/models/CLAUDE.md CHANGELOG.md
git commit -m "docs: unique_by, {parent_id}, path-uniqueness, CASCADE trade"
```

---

## Task 12: Full verification

- [ ] **Step 1:** `make check` → format + lint + typecheck clean.
- [ ] **Step 2:** `uv run ./scripts/run_tests.sh -k "unique_by or path_uniqueness or constraint or reconcil or parent or claim or toml" -q` → PASS.
- [ ] **Step 3:** `make test-fast > /tmp/test-unique-by.txt 2>&1` → no regression vs. baseline; inspect the file (do not pipe). Then `make frontend-check` → PASS.
- [ ] **Step 4:** Grep confirms no lingering `unique_per_user` references outside the compat inputs: `grep -rn "unique_per_user" clarinet/ # bash-ok` (expected: the mapping in `models/uniqueness.py`, the before-validators in `models/record_type.py`, the `RecordDef` kwarg in `config/primitives.py`, tests) and `grep -rn "unique_per_user" clarinet/frontend/src # bash-ok` (expected: no hits).

## Self-Review notes (author; updated after the 2026-07-21 review pass)

- Spec coverage: R(partitions / non-empty-or-None / `False`-off / default / quota / deprecation-on-every-path) → Tasks 1,3,4,8; R(bound-tuple rule / assignment enforcement / claimable listings / `shared_editing`) → Task 5 (+ Task 3 validator re-point); R(`{parent_id}` incl. None-render / validator / `{id}` fast-pass / multiple-exempt / `{origin_type}`-under-opt-out / per-file opt-out / PATCH guard) → Tasks 2,3,4,6; R(CASCADE / audit note / SQLite pragma + startup audit) → Tasks 3,9,11. All covered.
- Enforcement surface is FOUR points (create, inherit re-check, assign/claim, pool SQL filter) — Task 5 owns all of them; after Task 5 Step 5 nothing references the dropped column in SQL.
- `None` is a real "off" value distinct from unspecified — handled in Task 3 (`model_fields_set` for PATCH), Task 7 (reconciler explicit-None bypass at `reconciler.py:133`), Task 8 (TOML `false`).
- Type consistency: `canonical_unique_by` returns `frozenset | None`; DB rows hold the canonical **list** (table models skip validation) — every comparison canonicalizes both sides, and the engine JSON serializer (Task 3 Step 7) makes a stray frozenset un-crashable at flush. `RecordConstraintViolationError` used in both validator and repo, mapped to 409/`ConfigLoadError` by existing handlers. Level scope is always applied in Task 5's count and always checked in Task 6's coarser-level rule — never a `unique_by` member.
