"""
Pydantic models for RecordFlow action types.

Each action type is represented as a Pydantic model with a discriminated
``type`` literal field. The ``FlowAction`` union type enables type-safe
dispatch in the engine.
"""

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _ActionBase(BaseModel):
    """Shared configuration for all action models."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class CreateRecordAction(_ActionBase):
    """Action to create a new record of the specified type.

    Args:
        record_type_name: Name of the record type to create.
        series_uid: Optional series UID override.
        user_id: Optional user ID to assign.
        context_info: Optional context information string.
    """

    type: Literal["create_record"] = "create_record"
    record_type_name: str
    series_uid: str | None = None
    user_id: str | None = None
    context_info: str | None = None


class UpdateRecordAction(_ActionBase):
    """Action to update an existing record's status.

    Args:
        record_name: Name of the record type to update in context.
        status: Optional new status value.
    """

    type: Literal["update_record"] = "update_record"
    record_name: str
    status: str | None = None


class CallFunctionAction(_ActionBase):
    """Action to call a custom function with record context.

    Args:
        function: The callable to invoke.
        args: Positional arguments tuple.
        extra_kwargs: Keyword arguments dict passed to the function.
    """

    type: Literal["call_function"] = "call_function"
    function: Callable[..., Any]
    args: tuple[Any, ...] = ()
    extra_kwargs: dict[str, Any] = {}


class InvalidateRecordsAction(_ActionBase):
    """Action to invalidate records of specified types.

    Args:
        record_type_names: List of record type names to invalidate.
        mode: Invalidation mode — "hard" or "soft".
        callback: Optional callback function.
    """

    type: Literal["invalidate_records"] = "invalidate_records"
    record_type_names: list[str]
    mode: str = "hard"
    callback: Callable[..., Any] | None = None


class PipelineAction(_ActionBase):
    """Action to dispatch a pipeline task.

    Sends a message to a registered pipeline for distributed execution.

    Args:
        pipeline_name: Name of the registered pipeline to run.
        extra_payload: Additional key-value data merged into the pipeline message.
    """

    type: Literal["pipeline"] = "pipeline"
    pipeline_name: str
    extra_payload: dict[str, Any] = {}


FlowAction = (
    CreateRecordAction
    | UpdateRecordAction
    | CallFunctionAction
    | InvalidateRecordsAction
    | PipelineAction
)
