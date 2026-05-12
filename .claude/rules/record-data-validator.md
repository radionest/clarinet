---
paths: "plan/validators*.py"
---

# Record Data Validators — Python invariants beyond JSON Schema

## When to use

`RecordType.data_schema` (JSON Schema 2020-12) covers structural/type
constraints but cannot express:

- uniqueness of one field across array elements (`uniqueItems` compares the
  whole element);
- consistency across multiple fields (`start <= end`, fractions sum to 100);
- invariants that need DB access (e.g. value must be among IDs of related
  entities).

For those — use the `@record_validator` decorator in `plan/validators.py`.

## Contract

```python
from clarinet.exceptions.domain import FieldError, RecordDataValidationError
from clarinet.models.record import Record
from clarinet.services.record_data_validation import ValidatorContext, record_validator
from clarinet.types import RecordData


@record_validator("map_lesion_numbers.unique_new_id", run_on_partial=False)
async def validate_unique_new_id(
    record: Record, data: RecordData, ctx: ValidatorContext
) -> None:
    seen: dict[int, int] = {}
    errors: list[FieldError] = []
    for i, m in enumerate(data.get("mappings", [])):
        nid = m.get("new_id")
        if not isinstance(nid, int):
            # JSON-Schema validation has already run — types are correct.
            # Defensive skip for rare schema-vs-validator races.
            continue
        if nid in seen:
            errors.append(
                FieldError(
                    path=f"/mappings/{i}/new_id",
                    message=f"Duplicate value {nid}",
                    code="duplicate",
                    params={"value": nid, "first_seen": seen[nid]},
                )
            )
        else:
            seen[nid] = i
    if errors:
        raise RecordDataValidationError(errors)
```

Binding to a RecordType:

```python
# plan/record_types.py
from clarinet.flow import RecordDef

map_lesion_numbers = RecordDef(
    name="map-lesion-numbers",
    data_validators=["map_lesion_numbers.unique_new_id"],
    # ...
)
```

## Decorator parameters

| Param            | Default      | Meaning                                                                                                  |
|------------------|--------------|----------------------------------------------------------------------------------------------------------|
| `name`           | (required)   | Unique identifier referenced from `RecordDef.data_validators`. Double registration raises `ValueError` at import time. |
| `run_on_partial` | `False`      | When `True`, the validator runs on `prefill` (partial data). Default skips it — partial data may legitimately break full-document invariants. |

## `ValidatorContext`

```python
@dataclass(frozen=True, slots=True)
class ValidatorContext:
    record_repo: RecordRepository
    study_repo: StudyRepository
    user_repo: UserRepository
    record_type_repo: RecordTypeRepository
```

All repositories share the request's `AsyncSession` — **sequential `await`
only**, no `asyncio.gather` (see `clarinet/CLAUDE.md`).

## `FieldError`

```python
@dataclass(slots=True)
class FieldError:
    path: str       # JSON Pointer "/mappings/2/new_id" or "" for the document root
    message: str    # human-readable text (authored by the validator)
    code: str = "invalid"  # machine tag: "duplicate", "minimum", "required", ...
    params: dict[str, Any] = field(default_factory=dict)  # extra context for the frontend
```

`message` is in the project's language (the framework does not translate).
`code` is a stable tag the frontend can filter/group by. `params` is a
free-form JSON dict (e.g. the duplicate value, index of the first occurrence);
it is surfaced in the 422 payload for diagnostics but is not parsed by the
current Gleam decoder.

## Behavior

1. Validators run **after** JSON-Schema validation. If the schema fails,
   Python validators are not invoked — input data may have wrong types.
2. A RecordType may declare **multiple** validators — they run sequentially,
   **all errors aggregated** into a single `RecordDataValidationError`.
   UX rationale: the user sees every form issue in one pass.
3. On `prefill` (`validate_record_data_partial`), validators with
   `run_on_partial=False` are skipped.
4. Unknown name in `RecordDef.data_validators` → **fail-fast at startup**
   (`reconcile_config` raises `ConfigurationError`).
5. Runtime skip of an unknown name (e.g. after a hot config reload):
   `logger.error` + skip. The user's submit does not crash, but the
   validation effectively does not run. Trade-off: silently accepting is
   unsafe; fail-fast already protects the normal path at startup.
6. **Exception scope.** ``run_record_validators`` catches only
   ``RecordDataValidationError`` from validators and aggregates the
   ``FieldError`` list. **Any other exception propagates as a 500** —
   that is intentional: ``KeyError`` / ``AttributeError`` / DB errors
   inside a validator indicate a programmer bug or system failure, not
   bad user input, and should surface as such. Do **not** wrap validator
   bodies in defensive ``try/except`` to "convert" type errors into
   ``FieldError`` — the JSON-Schema stage already guarantees the data
   shape your validator sees, so type/key access on ``data`` is safe.

## HTTP 422 response

```json
{
  "detail": "Validation failed",
  "errors": [
    {"path": "/mappings/2/new_id", "message": "Duplicate value 3", "code": "duplicate", "params": {"value": 3, "first_seen": 0}}
  ]
}
```

`params` is omitted when `FieldError.params` is empty. JSON-Schema errors use
the same envelope (`code` = jsonschema validator name, e.g. `"minimum"`,
`"required"`, `"type"`). The schema validator caps at 10 errors per call
(see `_MAX_SCHEMA_ERRORS` in `clarinet/utils/validation.py`) to keep payload
size bounded.

Legacy `raise ValidationError("text")` still yields `{"detail": "text"}`
(no `errors` field). The frontend handles both shapes — `errors` is optional.

## Hooking up in a downstream project

In `settings.toml` (filename defaults to `validators.py`):

```toml
config_validators_file = "validators.py"   # optional, default
```

The file must live in `config_tasks_path` (commonly `./plan/`). It is loaded
**before** `reconcile_config` in the app lifespan
(`clarinet/api/app.py`) so that reconcile can validate every reference.

## Database migration

Adding `data_validators` to `RecordType` introduces a new nullable JSON column.
Clarinet is a framework — alembic migrations are generated and applied in the
downstream project. After upgrading Clarinet, run:

```sh
make db-migration  # → alembic revision --autogenerate -m "add data_validators column"
make db-upgrade
```

SQLAlchemy issues an explicit `SELECT col1, col2, ...` enumerated from model
metadata — without the migration, the column is absent in the DB and the
first `SELECT FROM recordtype` fails. Run the migration before deploying the
new Clarinet version.

## Testing

Unit-test the validator in isolation:

```python
import pytest
from clarinet.exceptions.domain import RecordDataValidationError
from plan.validators import validate_unique_new_id

async def test_duplicates_detected(record_factory, validator_ctx):
    record = await record_factory(type_name="map-lesion-numbers")
    data = {"mappings": [{"old_id": 1, "new_id": 3}, {"old_id": 2, "new_id": 3}]}
    with pytest.raises(RecordDataValidationError) as exc_info:
        await validate_unique_new_id(record, data, validator_ctx)
    assert exc_info.value.errors[0].path == "/mappings/1/new_id"
    assert exc_info.value.errors[0].code == "duplicate"
```

For tests that exercise the registry — request the
`isolated_validator_registry` fixture from `tests/conftest.py`. Pair with a
thin file-local autouse wrapper if every test in the file needs isolation:

```python
@pytest.fixture(autouse=True)
def _clean(isolated_validator_registry):
    pass
```

The fixture snapshots `_VALIDATOR_REGISTRY` before the test and restores it
after, mirroring the same pattern used by `test_schema_hydration.py` and
`test_slicer_context_hydration.py` for their respective registries.
