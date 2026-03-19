"""
RecordFlowEngine for executing record flow definitions.

This module provides the RecordFlowEngine class that monitors record status
changes and executes registered flows when their conditions are met.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from clarinet.utils.logger import logger

from .flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    FlowAction,
    InvalidateRecordsAction,
    PipelineAction,
    UpdateRecordAction,
)
from .flow_condition import FlowCondition
from .flow_file import FlowFileRecord
from .flow_record import FlowRecord
from .flow_result import _SELF

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from clarinet.client import ClarinetClient
    from clarinet.models import RecordRead, RecordStatus
    from clarinet.services.pipeline import PipelineMessage


@dataclass(frozen=True, slots=True)
class FlowContext:
    """Unified execution context for all flow trigger types."""

    record: RecordRead | None = None
    record_context: dict[str, RecordRead] | None = None
    patient_id: str | None = None
    study_uid: str | None = None
    series_uid: str | None = None
    file_name: str | None = None
    source_record: RecordRead | None = None

    @staticmethod
    def for_record(record: RecordRead, context: dict[str, RecordRead]) -> FlowContext:
        """Build context for a record-triggered flow."""
        return FlowContext(
            record=record,
            record_context=context,
            patient_id=record.patient.id,
            study_uid=record.study.study_uid if record.study else None,
            series_uid=record.series.series_uid if record.series else None,
        )

    @staticmethod
    def for_entity(
        patient_id: str,
        study_uid: str | None = None,
        series_uid: str | None = None,
    ) -> FlowContext:
        """Build context for an entity-creation flow."""
        return FlowContext(patient_id=patient_id, study_uid=study_uid, series_uid=series_uid)

    @staticmethod
    def for_file(
        file_name: str,
        patient_id: str,
        source_record: RecordRead | None = None,
    ) -> FlowContext:
        """Build context for a file-update flow."""
        return FlowContext(
            file_name=file_name,
            patient_id=patient_id,
            source_record=source_record,
        )


class RecordFlowEngine:
    """
    Engine for executing record flows based on record status changes.

    The engine monitors record status changes and executes registered flows
    when their conditions are met. It uses the ClarinetClient to interact
    with the API for creating/updating records.

    Example:
        engine = RecordFlowEngine(clarinet_client)
        engine.register_flow(my_flow)
        await engine.handle_record_status_change(record, old_status)
    """

    def __init__(self, clarinet_client: ClarinetClient):
        """Initialize the engine with a ClarinetClient.

        Args:
            clarinet_client: The API client for record operations.
        """
        self.clarinet_client = clarinet_client
        self.flows: dict[str, list[FlowRecord]] = {}
        self.entity_flows: dict[str, list[FlowRecord]] = {}
        self.file_flows: dict[str, list[FlowFileRecord]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()

    async def _ensure_authenticated(self) -> None:
        """Lazily authenticate the ClarinetClient on first use."""
        if self.clarinet_client._authenticated:
            return
        if self.clarinet_client.username and self.clarinet_client.password:
            await self.clarinet_client.login()

    def fire(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Schedule a coroutine as a fire-and-forget background task.

        Args:
            coro: Coroutine to run in the background.
        """
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    @staticmethod
    async def _maybe_await(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Call a function and await the result if it is a coroutine."""
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def register_flow(self, flow: FlowRecord | FlowFileRecord) -> None:
        """Register a flow definition.

        Args:
            flow: The FlowRecord or FlowFileRecord to register.

        Raises:
            ValueError: If the flow definition is invalid.
        """
        flow.validate()

        # Route file flows to separate registry
        if isinstance(flow, FlowFileRecord):
            if flow.file_name not in self.file_flows:
                self.file_flows[flow.file_name] = []
            self.file_flows[flow.file_name].append(flow)
            logger.info(f"Registered file flow for '{flow.file_name}' on update")
            return

        # Route entity flows to separate registry
        if flow.entity_trigger:
            if flow.entity_trigger not in self.entity_flows:
                self.entity_flows[flow.entity_trigger] = []
            self.entity_flows[flow.entity_trigger].append(flow)
            logger.info(f"Registered entity flow for '{flow.entity_trigger}' on creation")
            return

        # Group flows by record type name
        if flow.record_name not in self.flows:
            self.flows[flow.record_name] = []
        self.flows[flow.record_name].append(flow)

        if flow.data_update_trigger:
            logger.info(f"Registered flow for record type '{flow.record_name}' on data update")
        elif flow.file_change_trigger:
            logger.info(f"Registered flow for record type '{flow.record_name}' on file change")
        elif flow.status_trigger:
            logger.info(
                f"Registered flow for record type '{flow.record_name}' "
                f"on status '{flow.status_trigger}'"
            )
        else:
            logger.info(
                f"Registered flow for record type '{flow.record_name}' on any status change"
            )

    # ── Public handlers ───────────────────────────────────────────────────

    async def _dispatch_flows(
        self,
        record: RecordRead,
        trigger_label: str,
        predicate: Callable[[FlowRecord], bool],
    ) -> None:
        """Dispatch matching flows for a record event.

        Common logic for all record-level handlers: checks flow registry,
        builds context, and executes flows matching the predicate.

        Args:
            record: The record that triggered the event.
            trigger_label: Human-readable label for logging (e.g. "status change").
            predicate: Filter function selecting which flows to execute.
        """
        record_type_name = record.record_type.name

        if record_type_name not in self.flows:
            logger.debug(f"No flows registered for record type '{record_type_name}'")
            return

        logger.debug(
            f"Processing {trigger_label} flows for record {record.id} ({record_type_name})"
        )

        record_context = await self._get_record_context(record)

        for flow in self.flows[record_type_name]:
            if predicate(flow):
                logger.info(
                    f"Executing {trigger_label} flow for '{record_type_name}' (id={record.id})"
                )
                await self._execute_flow(flow, record, record_context)

    async def handle_record_status_change(
        self,
        record: RecordRead,
        old_status: RecordStatus | None = None,  # noqa: ARG002 - kept for future use
    ) -> None:
        """Handle a record status change and execute relevant flows.

        This is the main entry point for triggering flows. It should be called
        whenever a record's status changes.

        Args:
            record: The record that changed status.
            old_status: The previous status (optional).
        """
        current_status = record.status.value if hasattr(record.status, "value") else record.status

        await self._dispatch_flows(
            record,
            "status change",
            lambda f: (
                not (f.data_update_trigger or f.file_change_trigger)
                and (f.status_trigger is None or f.status_trigger == current_status)
            ),
        )

    async def handle_record_data_update(self, record: RecordRead) -> None:
        """Handle a data update on a finished record.

        Only executes flows with data_update_trigger=True. This is called
        when record data is modified via PATCH /records/{id}/data.

        Args:
            record: The record whose data was updated.
        """
        await self._dispatch_flows(record, "data update", lambda f: f.data_update_trigger)

    async def handle_record_file_change(self, record: RecordRead) -> None:
        """Handle a record file change and execute relevant flows.

        Only executes flows with file_change_trigger=True. This is called
        when file checksums are recomputed and changes are detected.

        Args:
            record: The record whose files changed.
        """
        await self._dispatch_flows(record, "file change", lambda f: f.file_change_trigger)

    async def handle_entity_created(
        self,
        entity_type: str,
        patient_id: str,
        study_uid: str | None = None,
        series_uid: str | None = None,
    ) -> None:
        """Handle an entity creation event and execute relevant flows.

        Called when a new patient, study, or series is created. Executes all
        entity flows registered for the given entity type.

        Args:
            entity_type: The entity type ("series", "study", or "patient").
            patient_id: The patient ID.
            study_uid: The study UID (for study and series entities).
            series_uid: The series UID (for series entities).
        """
        if entity_type not in self.entity_flows:
            logger.debug(f"No entity flows registered for '{entity_type}'")
            return

        logger.debug(
            f"Processing entity flows for '{entity_type}' "
            f"(patient={patient_id}, study={study_uid}, series={series_uid})"
        )

        ctx = FlowContext.for_entity(patient_id, study_uid, series_uid)
        for flow in self.entity_flows[entity_type]:
            for action in flow.actions:
                await self._execute_action(action, ctx)

    async def handle_file_update(
        self,
        file_name: str,
        patient_id: str,
        source_record: RecordRead | None = None,
    ) -> None:
        """Handle a project-level file change and execute relevant flows.

        Called when a pipeline task detects that a file's checksum changed,
        or when ``RecordService.submit_data()`` detects output file changes.

        Args:
            file_name: The logical file name (from file definition).
            patient_id: The patient whose storage contains the changed file.
            source_record: Record that produced the file change (for callbacks).
        """
        if file_name not in self.file_flows:
            logger.debug(f"No file flows registered for '{file_name}'")
            return

        logger.debug(f"Processing file update flows for '{file_name}' (patient={patient_id})")

        ctx = FlowContext.for_file(file_name, patient_id, source_record=source_record)
        for flow in self.file_flows[file_name]:
            if not flow.update_trigger:
                continue
            logger.info(f"Executing file flow for '{file_name}' (patient={patient_id})")
            for action in flow.actions:
                await self._execute_action(action, ctx)

    # ── Context helpers ───────────────────────────────────────────────────

    def _update_context_from_records(
        self, context: dict[str, RecordRead], records: list[RecordRead]
    ) -> None:
        """Merge records into context, keeping the latest by id for each record type."""
        for r in records:
            if not (r.record_type and r.record_type.name):
                continue
            name = r.record_type.name
            if name not in context or (r.id and r.id > context[name].id):
                context[name] = r

    async def _get_record_context(self, record: RecordRead) -> dict[str, RecordRead]:
        """Get all related records for evaluation context.

        Fetches records at all hierarchy levels for the same patient:
        patient-level, study-level, and series-level records. This enables
        cross-level invalidation (e.g. series change invalidating patient record).

        Args:
            record: The triggering record.

        Returns:
            Dictionary mapping record type names to their latest record instances.
        """
        await self._ensure_authenticated()
        context: dict[str, RecordRead] = {}

        try:
            if record.patient:
                records = await self.clarinet_client.find_records(
                    patient_id=record.patient.id, limit=1000
                )
                self._update_context_from_records(context, records)

            elif record.study:
                records = await self.clarinet_client.find_records(
                    study_uid=record.study.study_uid, limit=1000
                )
                self._update_context_from_records(context, records)

            elif record.series:
                records = await self.clarinet_client.find_records(
                    series_uid=record.series.series_uid, limit=1000
                )
                self._update_context_from_records(context, records)

        except Exception as e:
            logger.error(f"Error getting record context: {e}")

        return context

    # ── Flow execution ────────────────────────────────────────────────────

    async def _evaluate_and_run_condition(
        self,
        condition: FlowCondition,
        context: dict[str, RecordRead],
        ctx: FlowContext,
    ) -> bool | None:
        """Evaluate a condition and run its actions.

        Returns:
            True/False for the condition result, or None on error.
        """
        try:
            met = condition.evaluate(context)
        except Exception as e:
            logger.error(f"Error evaluating condition: {e}")
            return None
        if met:
            for action in condition.actions:
                await self._execute_action(action, ctx)
        return met

    async def _execute_flow(
        self, flow: FlowRecord, record: RecordRead, context: dict[str, RecordRead]
    ) -> None:
        """Execute a flow for a specific record.

        Args:
            flow: The flow definition to execute.
            record: The triggering record.
            context: Dictionary of related records.
        """
        # Add the current record to context
        context[flow.record_name] = record
        context[_SELF] = record

        ctx = FlowContext.for_record(record, context)

        # Execute unconditional actions
        for action in flow.actions:
            await self._execute_action(action, ctx)

        # Evaluate and execute conditional actions
        previous_condition_met = False
        match_group_met: dict[int, bool] = {}

        for condition in flow.conditions:
            group = condition.match_group

            if condition.is_else:
                if group is not None:
                    should_fire = not match_group_met.get(group, False)
                    # default() carries guard — evaluate it
                    if should_fire and condition.condition is not None:
                        try:
                            should_fire = condition.condition.evaluate(context)
                        except (TypeError, ValueError):
                            if condition.on_missing == "skip":
                                should_fire = False
                            else:
                                raise
                else:
                    should_fire = not previous_condition_met
                if should_fire:
                    for action in condition.actions:
                        await self._execute_action(action, ctx)
                if group is None:
                    break
                continue

            # Stop-on-first-match: skip if group already matched
            if group is not None and match_group_met.get(group, False):
                continue

            result = await self._evaluate_and_run_condition(condition, context, ctx)

            if group is not None:
                if result is True:
                    match_group_met[group] = True
            else:
                previous_condition_met = result is True

    # ── Unified action dispatcher ─────────────────────────────────────────

    async def _execute_action(self, action: FlowAction, ctx: FlowContext) -> None:
        """Execute a single action in the given context.

        Args:
            action: The action model instance.
            ctx: The unified flow context.
        """
        try:
            match action:
                case CreateRecordAction():
                    await self._create_record(action, ctx)
                case UpdateRecordAction() if ctx.record is not None:
                    await self._update_record(action, ctx)
                case InvalidateRecordsAction():
                    await self._invalidate_records(action, ctx)
                case CallFunctionAction():
                    await self._call_function(action, ctx)
                case PipelineAction() if ctx.file_name is None:
                    await self._dispatch_pipeline(action, ctx)
                case _:
                    logger.warning(f"Unsupported action type for context: {action.type}")
        except Exception as e:
            logger.error(f"Error executing action {action.type}: {e}")

    # ── Action implementations ────────────────────────────────────────────

    async def _create_record(self, action: CreateRecordAction, ctx: FlowContext) -> None:
        """Create a new record.

        Inherits ``user_id`` from the triggering record if not explicitly set
        (record context only). ``parent_record_id`` is passed only when a
        source record is present.

        Args:
            action: The CreateRecordAction with record details.
            ctx: The unified flow context.
        """
        await self._ensure_authenticated()
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
            result = await self.clarinet_client.create_record(record_create)
            logger.info(
                f"Created record '{action.record_type_name}' (id={result.id}) "
                f"for {ctx.study_uid or ctx.patient_id}"
            )
        except Exception as e:
            logger.error(f"Failed to create record '{action.record_type_name}': {e}")

    async def _update_record(self, action: UpdateRecordAction, ctx: FlowContext) -> None:
        """Update an existing record.

        Args:
            action: The UpdateRecordAction with target record name and status.
            ctx: The unified flow context (must have record_context).
        """
        await self._ensure_authenticated()
        from clarinet.models import RecordStatus

        context = ctx.record_context
        if context is None or action.record_name not in context:
            logger.warning(f"Record '{action.record_name}' not found in context for update")
            return

        target_record = context[action.record_name]

        # Update record status if specified
        if action.status is not None:
            try:
                status: str | RecordStatus = action.status
                if isinstance(status, str):
                    status = RecordStatus(status)

                await self.clarinet_client.update_record_status(target_record.id, status)
                logger.info(
                    f"Updated record '{action.record_name}' "
                    f"(id={target_record.id}) status to {status}"
                )
            except Exception as e:
                logger.error(f"Failed to update record status: {e}")

    async def _call_function(self, action: CallFunctionAction, ctx: FlowContext) -> None:
        """Call a custom function with context-appropriate kwargs.

        Args:
            action: The CallFunctionAction with function, args, and kwargs.
            ctx: The unified flow context.
        """
        if ctx.record is not None:
            kwargs: dict[str, Any] = {
                "record": ctx.record,
                "context": ctx.record_context,
                "client": self.clarinet_client,
            }
        elif ctx.file_name is not None:
            kwargs = {
                "file_name": ctx.file_name,
                "patient_id": ctx.patient_id,
                "source_record": ctx.source_record,
                "client": self.clarinet_client,
            }
        else:
            kwargs = {
                "patient_id": ctx.patient_id,
                "study_uid": ctx.study_uid,
                "series_uid": ctx.series_uid,
                "client": self.clarinet_client,
            }
        kwargs |= action.extra_kwargs

        try:
            await self._maybe_await(action.function, *action.args, **kwargs)
        except Exception as e:
            logger.error(f"Error calling function {action.function.__name__}: {e}")

    async def _dispatch_pipeline(self, action: PipelineAction, ctx: FlowContext) -> None:
        """Dispatch a task to a registered pipeline.

        Builds a PipelineMessage from the context and sends it to the named
        pipeline for distributed execution.

        Args:
            action: The PipelineAction with pipeline name and extra payload.
            ctx: The unified flow context.
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
        await self._run_pipeline(action, message, label)

    async def _run_pipeline(
        self,
        action: PipelineAction,
        message: PipelineMessage,
        context: str,
    ) -> None:
        """Look up and execute a registered pipeline.

        Args:
            action: The PipelineAction with pipeline name.
            message: Pre-built pipeline message.
            context: Human-readable description for logging.
        """
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

    # ── Invalidation ──────────────────────────────────────────────────────

    async def _invalidate_records(self, action: InvalidateRecordsAction, ctx: FlowContext) -> None:
        """Invalidate records of specified types.

        Unified entry point for record-triggered and file-triggered invalidation.
        Searches by patient_id (broadest scope) to find ALL records of target
        types, covering all hierarchy levels.

        Args:
            action: The InvalidateRecordsAction with target types, mode, and callback.
            ctx: The unified flow context.
        """
        await self._ensure_authenticated()
        for target_type_name in action.record_type_names:
            try:
                target_records = await self.clarinet_client.find_records(
                    patient_id=ctx.patient_id,
                    record_type_name=target_type_name,
                    limit=1000,
                )
            except Exception as e:
                logger.error(
                    f"Failed to find records of type '{target_type_name}' "
                    f"for patient {ctx.patient_id}: {e}"
                )
                continue

            for target in target_records:
                if ctx.record is not None:
                    await self._invalidate_from_record(target, ctx.record, action)
                elif ctx.file_name is not None:
                    await self._invalidate_from_file(target, ctx, action)

    async def _invalidate_from_record(
        self,
        target: RecordRead,
        source_record: RecordRead,
        action: InvalidateRecordsAction,
    ) -> None:
        """Invalidate a single target record triggered by another record.

        Skips self-invalidation. Passes source_record_id to the API.

        Args:
            target: The record to invalidate.
            source_record: The record that triggered the invalidation.
            action: The InvalidateRecordsAction with mode and callback.
        """
        if target.id == source_record.id:
            return

        try:
            await self.clarinet_client.invalidate_record(
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
            await self._maybe_await(
                action.callback,
                record=target,
                source_record=source_record,
                client=self.clarinet_client,
            )
        except Exception as e:
            logger.error(f"Error in invalidation callback for record {target.id}: {e}")

    async def _invalidate_from_file(
        self,
        target: RecordRead,
        ctx: FlowContext,
        action: InvalidateRecordsAction,
    ) -> None:
        """Invalidate a single target record triggered by a file change.

        Passes ``source_record_id`` when available (from ``submit_data`` path),
        otherwise uses a reason string only (from pipeline wrapper path).

        Args:
            target: The record to invalidate.
            ctx: The file flow context (must have file_name, may have source_record).
            action: The InvalidateRecordsAction with mode and callback.
        """
        source_record_id = ctx.source_record.id if ctx.source_record else None
        try:
            await self.clarinet_client.invalidate_record(
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
            await self._maybe_await(
                action.callback,
                record=target,
                source_record=ctx.source_record,
                file_name=ctx.file_name,
                client=self.clarinet_client,
            )
        except Exception as e:
            logger.error(f"Error in file invalidation callback for record {target.id}: {e}")
