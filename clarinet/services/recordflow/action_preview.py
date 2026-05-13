"""ActionPreview — read-only description of what a flow action *would* do.

Plan mode of :class:`RecordFlowEngine` skips real action execution and instead
appends an :class:`ActionPreview` per dispatched action. The preview is the
return type of public ``plan_record_*`` / ``plan_file_update`` /
``plan_entity_created`` methods and the body of the
``POST /api/admin/workflow/dry-run`` endpoint.

It carries enough context to render a dry-run UI panel without exposing the
underlying ``Callable`` (for ``call_function``) or any unsafe internals.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from .flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    FlowAction,
    InvalidateRecordsAction,
    PipelineAction,
    UpdateRecordAction,
)

if TYPE_CHECKING:
    from .flow_context import FlowContext


ActionType = Literal[
    "create_record",
    "update_record",
    "invalidate_records",
    "call_function",
    "pipeline",
]


class ActionPreview(BaseModel):
    """Serializable description of one would-be action in plan mode.

    `summary` is a one-line human-readable label suitable for a dry-run list
    item. `details` carries action-type-specific fields (record names, mode,
    pipeline payload) for richer rendering. Trigger fields point to the
    record/entity/file that caused this action so the UI can group previews
    by source when several flows fire at once.
    """

    action_type: ActionType
    summary: str
    target: str | None = None

    details: dict[str, Any] = Field(default_factory=dict)

    trigger_record_id: int | None = None
    trigger_record_type: str | None = None
    patient_id: str | None = None
    study_uid: str | None = None
    series_uid: str | None = None
    file_name: str | None = None


def _trigger_fields(ctx: FlowContext) -> dict[str, Any]:
    return {
        "trigger_record_id": ctx.record.id if ctx.record else None,
        "trigger_record_type": (
            ctx.record.record_type.name if ctx.record and ctx.record.record_type else None
        ),
        "patient_id": ctx.patient_id,
        "study_uid": ctx.study_uid,
        "series_uid": ctx.series_uid,
        "file_name": ctx.file_name,
    }


def action_to_preview(action: FlowAction, ctx: FlowContext) -> ActionPreview:
    """Map a runtime :class:`FlowAction` + context to a serializable preview."""
    common = _trigger_fields(ctx)

    match action:
        case CreateRecordAction():
            return ActionPreview(
                action_type="create_record",
                summary=f"Create record '{action.record_type_name}'",
                target=action.record_type_name,
                details={
                    "record_type_name": action.record_type_name,
                    "series_uid": action.series_uid,
                    "user_id": action.user_id,
                    "parent_record_id": action.parent_record_id,
                    "inherit_user": action.inherit_user,
                    "context_info": action.context_info,
                },
                **common,
            )
        case UpdateRecordAction():
            status_part = f" status to '{action.status}'" if action.status else ""
            return ActionPreview(
                action_type="update_record",
                summary=(
                    f"Update '{action.record_name}'{status_part} (strategy='{action.strategy}')"
                ),
                target=action.record_name,
                details={
                    "record_name": action.record_name,
                    "status": action.status,
                    "strategy": action.strategy,
                },
                **common,
            )
        case InvalidateRecordsAction():
            targets = ", ".join(action.record_type_names) or "<none>"
            return ActionPreview(
                action_type="invalidate_records",
                summary=f"Invalidate ({action.mode}): {targets}",
                target=None,
                details={
                    "record_type_names": list(action.record_type_names),
                    "mode": action.mode,
                    "has_callback": action.callback is not None,
                },
                **common,
            )
        case CallFunctionAction():
            fname = getattr(action.function, "__name__", repr(action.function))
            return ActionPreview(
                action_type="call_function",
                summary=f"Call function '{fname}'",
                target=fname,
                details={
                    "function_name": fname,
                    "function_module": getattr(action.function, "__module__", None),
                    "args_count": len(action.args),
                    "kwarg_keys": sorted(action.extra_kwargs.keys()),
                },
                **common,
            )
        case PipelineAction():
            return ActionPreview(
                action_type="pipeline",
                summary=f"Dispatch pipeline '{action.pipeline_name}'",
                target=action.pipeline_name,
                details={
                    "pipeline_name": action.pipeline_name,
                    "extra_payload": dict(action.extra_payload),
                },
                **common,
            )
        case _:
            # Fail loud so a missed FlowAction variant is caught in tests
            # instead of mislabelled as call_function in the UI.
            raise TypeError(
                f"action_to_preview: unhandled FlowAction subtype "
                f"{type(action).__name__!r}; extend the match statement."
            )
