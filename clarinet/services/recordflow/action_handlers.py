"""Implementations of RecordFlow actions.

Module-level coroutines invoked by ``RecordFlowEngine._execute_action``.
Each handler receives the engine instance for access to the API client and
authentication helpers, the action model, and the unified flow context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import RecordLimitReachedError, RecordUniquePerUserError
from clarinet.utils.logger import logger

from .flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    InvalidateRecordsAction,
    PipelineAction,
    UpdateRecordAction,
)
from .flow_context import FlowContext

if TYPE_CHECKING:
    from clarinet.models import RecordRead
    from clarinet.services.pipeline import PipelineMessage

    from .engine import RecordFlowEngine


# Machine-readable codes returned by the API for expected constraint violations.
# Single source of truth: error_code attributes on domain exception classes.
_EXPECTED_CONFLICT_CODES = frozenset(
    {
        RecordLimitReachedError.error_code,
        RecordUniquePerUserError.error_code,
    }
)


def _is_expected_conflict(exc: BaseException) -> bool:
    """Check if exception is a 409 with a known constraint violation code."""
    from clarinet.client import ClarinetAPIError

    if not isinstance(exc, ClarinetAPIError) or exc.status_code != 409:
        return False
    detail = exc.detail
    if isinstance(detail, dict):
        return detail.get("code") in _EXPECTED_CONFLICT_CODES
    return False


async def create_record(
    engine: RecordFlowEngine, action: CreateRecordAction, ctx: FlowContext
) -> None:
    """Create a new record.

    Inherits ``user_id`` from the triggering record if not explicitly set
    (record context only). ``parent_record_id`` is passed only when a
    source record is present.
    """
    await engine._ensure_authenticated()
    from clarinet.models import RecordCreate

    series_uid = action.series_uid or ctx.series_uid
    user_id = action.user_id
    parent_record_id = action.parent_record_id

    if ctx.record is not None:
        # Inherit user_id only if explicitly requested.
        # Linked records get user_id via API-level parent inheritance.
        if action.inherit_user and user_id is None and ctx.record.user_id is not None:
            user_id = str(ctx.record.user_id)

        default_info = (
            f"Created by flow from record {ctx.record.record_type.name} (id={ctx.record.id})"
        )
    else:
        default_info = (
            f"Created by entity flow on "
            f"{ctx.series_uid or ctx.study_uid or ctx.patient_id} creation"
        )

    try:
        record_create = RecordCreate(
            record_type_name=action.record_type_name,
            patient_id=ctx.patient_id,
            study_uid=ctx.study_uid,
            series_uid=series_uid,
            user_id=user_id,
            parent_record_id=parent_record_id,
            context_info=action.context_info or default_info,
        )
        result = await engine.clarinet_client.create_record(record_create)
        logger.info(
            f"Created record '{action.record_type_name}' (id={result.id}) "
            f"for {ctx.study_uid or ctx.patient_id}"
        )
    except Exception as e:
        if _is_expected_conflict(e):
            logger.warning(f"Record '{action.record_type_name}' skipped (expected constraint): {e}")
        else:
            logger.error(f"Failed to create record '{action.record_type_name}': {e}")


async def update_record(
    engine: RecordFlowEngine, action: UpdateRecordAction, ctx: FlowContext
) -> None:
    """Update existing records selected by ``strategy``.

    ``strategy='single'``: requires exactly one record of the given type
    in context — skips with an error log if 0 or >1. ``strategy='all'``:
    applies the update to every matching record.
    """
    await engine._ensure_authenticated()
    from clarinet.models import RecordStatus

    context = ctx.record_context
    if context is None:
        logger.warning(f"No record context for update_record('{action.record_name}')")
        return

    targets = context.get(action.record_name, [])
    if not targets:
        logger.warning(f"Record '{action.record_name}' not found in context for update")
        return

    if action.strategy == "single" and len(targets) > 1:
        ids = [t.id for t in targets[:5]]
        ids_suffix = ", ..." if len(targets) > 5 else ""
        logger.error(
            f"update_record('{action.record_name}', strategy='single') "
            f"ambiguous: found {len(targets)} records in context "
            f"(ids: {ids}{ids_suffix}). Use strategy='all' to update every match."
        )
        return

    if action.status is None:
        logger.warning(
            f"update_record('{action.record_name}') has no status to apply — "
            f"action is a no-op; remove it or pass status="
        )
        return

    try:
        status: str | RecordStatus = action.status
        if isinstance(status, str):
            status = RecordStatus(status)
    except Exception as e:
        logger.error(f"Invalid status for update_record: {e}")
        return

    for target in targets:
        try:
            await engine.clarinet_client.update_record_status(target.id, status)
            logger.info(
                f"Updated record '{action.record_name}' (id={target.id}) status to {status}"
            )
        except Exception as e:
            logger.error(f"Failed to update record status: {e}")


async def call_function(
    engine: RecordFlowEngine, action: CallFunctionAction, ctx: FlowContext
) -> None:
    """Call a custom function with context-appropriate kwargs."""
    if ctx.record is not None:
        kwargs: dict[str, Any] = {
            "record": ctx.record,
            "context": ctx.record_context,
            "client": engine.clarinet_client,
        }
    elif ctx.file_name is not None:
        kwargs = {
            "file_name": ctx.file_name,
            "patient_id": ctx.patient_id,
            "source_record": ctx.source_record,
            "client": engine.clarinet_client,
        }
    else:
        kwargs = {
            "patient_id": ctx.patient_id,
            "study_uid": ctx.study_uid,
            "series_uid": ctx.series_uid,
            "client": engine.clarinet_client,
        }
    kwargs |= action.extra_kwargs

    try:
        await engine._maybe_await(action.function, *action.args, **kwargs)
    except Exception as e:
        if _is_expected_conflict(e):
            logger.warning(f"Expected conflict in function {action.function.__name__}: {e}")
        else:
            logger.error(f"Error calling function {action.function.__name__}: {e}")


async def dispatch_pipeline(action: PipelineAction, ctx: FlowContext) -> None:
    """Dispatch a task to a registered pipeline.

    Builds a PipelineMessage from the context and sends it to the named
    pipeline for distributed execution.
    """
    from clarinet.services.pipeline import PipelineMessage

    message = PipelineMessage(
        patient_id=ctx.patient_id or "",
        study_uid=ctx.study_uid or "",
        series_uid=ctx.series_uid,
        record_id=ctx.record.id if ctx.record else None,
        record_type_name=(
            ctx.record.record_type.name if ctx.record and ctx.record.record_type else None
        ),
        payload=action.extra_payload,
    )
    label = (
        f"record {ctx.record.id} ({ctx.record.record_type.name})"
        if ctx.record
        else f"entity (patient={ctx.patient_id})"
    )
    await run_pipeline(action, message, label)


async def run_pipeline(
    action: PipelineAction,
    message: PipelineMessage,
    context: str,
) -> None:
    """Look up and execute a registered pipeline."""
    from clarinet.services.pipeline import get_pipeline

    pipeline = get_pipeline(action.pipeline_name)
    if pipeline is None:
        logger.error(
            f"Pipeline '{action.pipeline_name}' not found. "
            f"Ensure it is registered before RecordFlow triggers it."
        )
        return

    try:
        await pipeline.run(message)
        logger.info(f"Dispatched pipeline '{action.pipeline_name}' for {context}")
    except Exception as e:
        logger.error(f"Failed to dispatch pipeline '{action.pipeline_name}': {e}")


async def invalidate_records(
    engine: RecordFlowEngine, action: InvalidateRecordsAction, ctx: FlowContext
) -> None:
    """Invalidate records of specified types.

    Unified entry point for record-triggered and file-triggered invalidation.
    Searches by patient_id (broadest scope) to find ALL records of target
    types, covering all hierarchy levels.
    """
    await engine._ensure_authenticated()
    for target_type_name in action.record_type_names:
        try:
            target_records = [
                r
                async for r in engine.clarinet_client.iter_records(
                    patient_id=ctx.patient_id,
                    record_type_name=target_type_name,
                )
            ]
        except Exception as e:
            logger.error(
                f"Failed to find records of type '{target_type_name}' "
                f"for patient {ctx.patient_id}: {e}"
            )
            continue

        for target in target_records:
            if ctx.record is not None:
                await invalidate_from_record(engine, target, ctx.record, action)
            elif ctx.file_name is not None:
                await invalidate_from_file(engine, target, ctx, action)


async def invalidate_from_record(
    engine: RecordFlowEngine,
    target: RecordRead,
    source_record: RecordRead,
    action: InvalidateRecordsAction,
) -> None:
    """Invalidate a single target record triggered by another record.

    Skips self-invalidation. Passes source_record_id to the API.
    """
    if target.id == source_record.id:
        return

    try:
        await engine.clarinet_client.invalidate_record(
            record_id=target.id,
            mode=action.mode,
            source_record_id=source_record.id,
        )
        logger.info(
            f"Invalidated record '{target.record_type.name}' (id={target.id}) "
            f"mode='{action.mode}', triggered by record {source_record.id}"
        )
    except Exception as e:
        logger.error(
            f"Failed to invalidate record '{target.record_type.name}' (id={target.id}): {e}"
        )
        return

    if action.callback is None:
        return
    try:
        await engine._maybe_await(
            action.callback,
            record=target,
            source_record=source_record,
            client=engine.clarinet_client,
        )
    except Exception as e:
        logger.error(f"Error in invalidation callback for record {target.id}: {e}")


async def invalidate_from_file(
    engine: RecordFlowEngine,
    target: RecordRead,
    ctx: FlowContext,
    action: InvalidateRecordsAction,
) -> None:
    """Invalidate a single target record triggered by a file change.

    Passes ``source_record_id`` when available (from ``submit_data`` path),
    otherwise uses a reason string only (from pipeline wrapper path).
    """
    source_record_id = ctx.source_record.id if ctx.source_record else None
    try:
        await engine.clarinet_client.invalidate_record(
            record_id=target.id,
            mode=action.mode,
            source_record_id=source_record_id,
            reason=f"Invalidated by file change: {ctx.file_name}",
        )
        logger.info(
            f"Invalidated record '{target.record_type.name}' (id={target.id}) "
            f"mode='{action.mode}', triggered by file '{ctx.file_name}'"
        )
    except Exception as e:
        logger.error(
            f"Failed to invalidate record '{target.record_type.name}' (id={target.id}): {e}"
        )
        return

    if action.callback is None:
        return
    try:
        await engine._maybe_await(
            action.callback,
            record=target,
            source_record=ctx.source_record,
            file_name=ctx.file_name,
            client=engine.clarinet_client,
        )
    except Exception as e:
        logger.error(f"Error in file invalidation callback for record {target.id}: {e}")
