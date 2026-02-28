# Plan: Typed payload validation for Pipeline Service

## Context

The pipeline service passes data between steps via `PipelineMessage.payload: dict[str, Any]`. Three problems:
1. **Late error detection** — task returning wrong type (e.g. `str`) is caught in `PipelineChainMiddleware._dispatch_next_step` (line 403) and silently routed to DLQ. Error should be raised in the task itself.
2. **No payload validation** — `message.payload.get("working_dir")` + manual `if not working_dir` instead of Pydantic validation (see `air_pipeline_flow.py:156-158`).
3. **Implicit contracts** — which keys each step expects/produces is only discoverable from reading task source code.

## Design

### 1. `pipeline_task()` decorator — single decorator replacing `@broker.task()`

**New file:** `src/services/pipeline/task.py`

A decorator factory that:
- Internally calls `get_broker().task(**kwargs)` (so tasks are registered on the singleton broker as before)
- Changes task function signature: tasks receive `(message: PipelineMessage)` or `(message: PipelineMessage, payload: T)` instead of `(msg: dict)`
- Validates input payload against declared model on entry
- Validates output payload against declared model on exit
- Wraps return value back into `PipelineMessage.model_dump()` for the wire format

```python
from pydantic import BaseModel

PayloadT = TypeVar("PayloadT", bound=BaseModel)

def pipeline_task(
    *,
    input: type[BaseModel] | None = None,
    output: type[BaseModel] | None = None,
    **task_kwargs: Any,
) -> Callable:
```

**Wrapper logic:**

```python
async def wrapper(msg: dict) -> dict:
    # 1. Parse message
    message = PipelineMessage.model_validate(msg)

    # 2. Validate input & call task
    if input is not None:
        validated_input = input.model_validate(message.payload)
        result = await func(message, validated_input)
    else:
        result = await func(message)

    # 3. Validate output
    if result is None:
        # Terminal step or no output — preserve existing payload
        return message.model_dump()

    if isinstance(result, BaseModel):
        if output is not None and not isinstance(result, output):
            raise PipelineStepError(
                func.__name__,
                f"declared output={output.__name__}, returned {type(result).__name__}",
            )
        message.payload = result.model_dump()
        return message.model_dump()

    raise PipelineStepError(
        func.__name__,
        f"must return BaseModel or None, got {type(result).__name__}",
    )
```

`PipelineStepError` (already in `src/exceptions/domain.py:416-423`) takes `(step_name, reason)`. Gets caught by SmartRetryMiddleware -> DeadLetterMiddleware with clear error.

### 2. `validate_payload()` method on PipelineMessage

**File:** `src/services/pipeline/message.py`

For manual use in `@broker.task()` tasks (tests, non-pipeline tasks):

```python
def validate_payload(self, model: type[PayloadT]) -> PayloadT:
    """Validate payload against a Pydantic model.

    Args:
        model: Pydantic model class to validate against.

    Returns:
        Validated model instance.

    Raises:
        pydantic.ValidationError: If payload doesn't match the model.
    """
    return model.model_validate(self.payload)
```

### 3. Payload models for air pipeline

**File:** `examples/demo/air_pipeline_flow.py`

Inheritance chain accumulates fields through steps:

```python
class AirSegmentPayload(BaseModel):
    """Produced by segment_air."""
    working_dir: str

class AirRecordPayload(AirSegmentPayload):
    """Produced by create_air_record. Consumed by calculate_air_volume."""
    record_id: int
```

Migrated tasks:

```python
@pipeline_task(output=AirSegmentPayload)
async def segment_air(message: PipelineMessage) -> AirSegmentPayload:
    # message.patient_id, message.series_uid — typed, available
    ...
    return AirSegmentPayload(working_dir=str(working_path))

@pipeline_task(input=AirSegmentPayload, output=AirRecordPayload)
async def create_air_record(
    message: PipelineMessage, payload: AirSegmentPayload
) -> AirRecordPayload:
    # payload.working_dir — str, validated
    ...
    return AirRecordPayload(working_dir=payload.working_dir, record_id=record.id)

@pipeline_task(input=AirRecordPayload)
async def calculate_air_volume(
    message: PipelineMessage, payload: AirRecordPayload
) -> None:
    # payload.working_dir, payload.record_id — typed, validated
    ...
    # Terminal step, returns None
```

### 4. Export from `__init__.py`

**File:** `src/services/pipeline/__init__.py`

Add `pipeline_task` to imports and `__all__`.

## Files to create/modify

| File | Action | Change |
|------|--------|--------|
| `src/services/pipeline/task.py` | **Create** | `pipeline_task()` decorator |
| `src/services/pipeline/message.py` | Edit | Add `validate_payload()` method |
| `src/services/pipeline/__init__.py` | Edit | Export `pipeline_task` |
| `examples/demo/air_pipeline_flow.py` | Edit | Payload models + migrate tasks to new decorator |
| `tests/test_pipeline.py` | Edit | Tests for `validate_payload()`, `pipeline_task()` decorator |
| `src/services/pipeline/CLAUDE.md` | Edit | Document payload model convention and `pipeline_task` usage |

## What stays unchanged

- `PipelineMessage.payload` stays `dict[str, Any]` — wire format for TaskIQ
- `@broker.task()` still works — for tests and non-pipeline tasks
- Middleware code — no changes needed (already handles dict result correctly)
- Integration tests — they use `@broker.task()` directly, unaffected
- `PipelineAction.extra_payload` — extra keys in payload are ignored by Pydantic validation (default `extra="ignore"`)

## Verification

1. `make test` — existing unit + integration tests pass unchanged
2. New unit tests in `tests/test_pipeline.py`:
   - `validate_payload()` with valid data -> returns typed model
   - `validate_payload()` with missing field -> raises `ValidationError`
   - `pipeline_task()` with correct return -> produces valid PipelineMessage dict
   - `pipeline_task()` with wrong return type -> raises `PipelineStepError`
   - `pipeline_task()` with mismatched output model -> raises `PipelineStepError`
   - `pipeline_task(input=Model)` with invalid payload -> raises `ValidationError`
3. `make lint && make typecheck` — no regressions
4. Verify `_load_task_modules()` still picks up tasks decorated with `@pipeline_task()` (they're registered on the singleton broker)
