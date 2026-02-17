# RecordFlow Engine: Railway-Oriented Programming Refactoring

## Overview

Refactor `src/services/recordflow/engine.py` to use railway-oriented programming with `dry-python/returns` library. Focus on engine only, with short-circuit error handling.

## Problem Statement

Current engine has problematic error handling:
- Silent failures: exceptions caught, logged, swallowed
- Condition evaluation errors treated as "false" (wrong semantics)
- No visibility into which actions succeeded/failed
- Generic `except Exception` blocks everywhere

## Solution: Railway-Oriented Programming with Result Types

### Error Types (add to `src/exceptions/domain.py`)

```python
class RecordFlowError(ClarinetError):
    """Base exception for RecordFlow engine errors."""

class ContextFetchError(RecordFlowError):
    """Failed to fetch record context."""
    record_id: int
    reason: str

class ConditionEvaluationError(RecordFlowError):
    """Condition evaluation failed (not same as condition being False)."""
    condition: str
    reason: str

class ActionExecutionError(RecordFlowError):
    """Base for action failures."""
    action_type: str
    reason: str

class RecordCreationError(ActionExecutionError):
    record_type_name: str

class RecordUpdateError(ActionExecutionError):
    record_name: str

class FunctionCallError(ActionExecutionError):
    function_name: str
```

### Success Types (add to `engine.py`)

```python
@dataclass
class FlowExecutionSuccess:
    flow_name: str
    actions_executed: int
    records_created: list[int]
    records_updated: list[int]

@dataclass
class ActionSuccess:
    action_type: str
    record_id: int | None = None
```

### New Method Signatures

```python
async def handle_record_status_change(
    self, record: RecordRead, old_status: RecordStatus | None = None
) -> FutureResult[list[FlowExecutionSuccess], RecordFlowError]

async def _get_record_context(
    self, record: RecordRead
) -> FutureResult[RecordContext, ContextFetchError]

async def _execute_flow(
    self, flow: FlowRecord, record: RecordRead, context: RecordContext
) -> FutureResult[FlowExecutionSuccess, RecordFlowError]

async def _execute_action(
    self, action: dict, record: RecordRead, context: RecordContext
) -> FutureResult[ActionSuccess, ActionExecutionError]

def _evaluate_condition(
    self, condition: FlowCondition, context: RecordContext
) -> Result[bool, ConditionEvaluationError]
```

### Composition Pattern

Use pattern matching on Result for short-circuit:

```python
result = await self._execute_action(action, record, context)
match result:
    case Success(success):
        actions_executed += 1
    case Failure(error):
        return FutureResult.from_failure(error)  # Short-circuit
```

## Implementation Steps

### Step 1: Add dependency
Add to `pyproject.toml`:
```toml
"returns>=0.23.0",
```

### Step 2: Add error types
File: `src/exceptions/domain.py`
- Add `RecordFlowError` base class
- Add `ContextFetchError`, `ConditionEvaluationError`
- Add `ActionExecutionError` and subclasses

### Step 3: Export exceptions
File: `src/exceptions/__init__.py`

### Step 4: Add success dataclasses
File: `src/services/recordflow/engine.py`
- Add `FlowExecutionSuccess`
- Add `ActionSuccess`

### Step 5: Refactor `_get_record_context()`
- Return `FutureResult[RecordContext, ContextFetchError]`
- Convert API errors to typed Failure

### Step 6: Add `_evaluate_condition()` helper
- Return `Result[bool, ConditionEvaluationError]`
- Separate "evaluation failed" from "condition is false"

### Step 7: Refactor action methods
- `_create_record()` -> `FutureResult[ActionSuccess, RecordCreationError]`
- `_update_record()` -> `FutureResult[ActionSuccess, RecordUpdateError]`
- `_call_function()` -> `FutureResult[ActionSuccess, FunctionCallError]`

### Step 8: Refactor `_execute_action()` dispatcher
- Return `FutureResult[ActionSuccess, ActionExecutionError]`
- Dispatch and propagate Result

### Step 9: Refactor `_execute_flow()`
- Short-circuit on first Failure
- Accumulate successes in FlowExecutionSuccess

### Step 10: Refactor `handle_record_status_change()`
- Return `FutureResult[list[FlowExecutionSuccess], RecordFlowError]`
- Chain with `.bind_async()`

### Step 11: Update router integration
File: `src/api/routers/record.py`

Add wrapper for background tasks:
```python
async def execute_recordflow_with_logging(
    engine: RecordFlowEngine,
    record: RecordRead,
    old_status: RecordStatus | None,
) -> None:
    result = await engine.handle_record_status_change(record, old_status)
    match result:
        case Success(successes):
            for s in successes:
                logger.info(f"Flow '{s.flow_name}' completed: {s.actions_executed} actions")
        case Failure(error):
            logger.error(f"RecordFlow failed: {error}")
```

### Step 12: Update exports
File: `src/services/recordflow/__init__.py`
- Export `FlowExecutionSuccess`, `ActionSuccess`

## Files to Modify

| File | Changes |
|------|---------|
| `pyproject.toml` | Add `returns` dependency |
| `src/exceptions/domain.py` | Add RecordFlow error types |
| `src/exceptions/__init__.py` | Export new exceptions |
| `src/services/recordflow/engine.py` | Main refactoring target |
| `src/services/recordflow/__init__.py` | Export success types |
| `src/api/routers/record.py` | Update integration |

## Benefits

1. **Explicit errors**: Every signature shows what can fail
2. **Short-circuit**: First error stops execution immediately
3. **Typed errors**: Different error types enable specific handling
4. **Visibility**: Callers see exactly what succeeded/failed
5. **Composition**: Railway pattern makes complex flows readable

## Key Insight

The current code treats evaluation errors as "condition = false" which is semantically wrong. With Result types, we distinguish between:
- `Success(False)` - condition evaluated successfully to false
- `Failure(ConditionEvaluationError)` - evaluation itself failed
