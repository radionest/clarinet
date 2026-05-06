"""
RecordFlowEngine for executing record flow definitions.

This module provides the RecordFlowEngine class that monitors record status
changes and executes registered flows when their conditions are met.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from clarinet.models.base import DicomQueryLevel
from clarinet.utils.logger import logger

from . import action_handlers
from .flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    FlowAction,
    InvalidateRecordsAction,
    PipelineAction,
    UpdateRecordAction,
)
from .flow_condition import FlowCondition
from .flow_context import FlowContext
from .flow_file import FlowFileRecord
from .flow_record import FlowRecord
from .flow_result import _SELF

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from clarinet.client import ClarinetClient
    from clarinet.models import RecordRead, RecordStatus


def _is_ssl_error(exc: BaseException) -> bool:
    """Check if an exception chain contains an SSL certificate error."""
    import ssl

    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ or current.__context__
    return False


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

    # Class-level default so AsyncMock(spec=RecordFlowEngine) sees this attribute.
    _api_verified: bool = False

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

    async def _ensure_api_reachable(self) -> None:
        """One-time API connectivity check on first use (when api_base_url is set)."""
        if self._api_verified:
            return
        self._api_verified = True  # fire-once: don't retry on failure

        from clarinet.settings import settings

        if not settings.api_base_url:
            return

        import httpx

        try:
            response = await self.clarinet_client.client.get("/health")
            response.raise_for_status()
        except httpx.ConnectError as e:
            if _is_ssl_error(e):
                logger.error(
                    f"RecordFlow: SSL verification failed for "
                    f"{settings.effective_api_base_url}. "
                    f"Set api_verify_ssl = false for self-signed certificates"
                )
            else:
                logger.error(
                    f"RecordFlow: cannot connect to API at {settings.effective_api_base_url}: {e}"
                )
        except httpx.HTTPError as e:
            logger.error(f"RecordFlow: API check failed for {settings.effective_api_base_url}: {e}")

    async def _ensure_authenticated(self) -> None:
        """Lazily authenticate the ClarinetClient on first use."""
        await self._ensure_api_reachable()
        if self.clarinet_client._authenticated:
            return
        if self.clarinet_client.service_token:
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
                # Each flow gets its own shallow-copied dict so per-flow mutations
                # in _execute_flow (trigger insertion, _SELF) don't leak across
                # sibling flows registered on the same record type.
                await self._execute_flow(flow, record, dict(record_context))

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

    @staticmethod
    def _record_in_tree(
        record: RecordRead,
        trigger_level: DicomQueryLevel | None,
        trigger_study_uid: str | None,
        trigger_series_uid: str | None,
    ) -> bool:
        """Tree-filter: keep records on ancestors and subtree of trigger.

        With ``trigger_level``:
        - PATIENT trigger keeps every record of the patient (entire subtree).
        - STUDY trigger keeps PATIENT-level + records of the same study (any
          series_uid, since sibling series belong to the same subtree).
        - SERIES trigger keeps PATIENT-level + STUDY-level of the same study
          + SERIES-level of the same series. Sibling series are out of scope.

        PATIENT-level records always pass — they are the topmost ancestor of
        any trigger. STUDY-/SERIES-level records require the trigger to expose
        the matching ``study_uid`` / ``series_uid``; if not (e.g. a malformed
        trigger with ``record_type is None``) they are rejected defensively.
        """
        if record.record_type is None:
            return False
        record_level = record.record_type.level
        if record_level == DicomQueryLevel.PATIENT:
            return True
        if record_level == DicomQueryLevel.STUDY:
            if trigger_level == DicomQueryLevel.PATIENT:
                return True
            if trigger_study_uid is None:
                return False
            return record.study_uid == trigger_study_uid
        if record_level == DicomQueryLevel.SERIES:
            if trigger_level == DicomQueryLevel.PATIENT:
                return True
            if trigger_level == DicomQueryLevel.STUDY:
                if trigger_study_uid is None:
                    return False
                return record.study_uid == trigger_study_uid
            # SERIES trigger: keep only the same series.
            if trigger_series_uid is None:
                return False
            return record.series_uid == trigger_series_uid
        return False

    def _build_context_from_records(
        self,
        records: list[RecordRead],
        trigger: RecordRead,
    ) -> dict[str, list[RecordRead]]:
        """Filter and group records by type for the trigger's tree slice."""
        trigger_level = trigger.record_type.level if trigger.record_type else None
        trigger_study_uid = trigger.study_uid
        trigger_series_uid = trigger.series_uid

        context: dict[str, list[RecordRead]] = {}
        for r in records:
            if not (r.record_type and r.record_type.name):
                continue
            if not self._record_in_tree(r, trigger_level, trigger_study_uid, trigger_series_uid):
                continue
            context.setdefault(r.record_type.name, []).append(r)

        # Stable order by id (helps deterministic picking when callers iterate).
        for lst in context.values():
            lst.sort(key=lambda x: x.id or 0)
        return context

    async def _get_record_context(self, record: RecordRead) -> dict[str, list[RecordRead]]:
        """Build the evaluation context for a record-triggered flow.

        The context contains records on ``ancestors(trigger)`` and ``subtree(trigger)``
        in the PATIENT → STUDY → SERIES DICOM hierarchy, grouped by record
        type name. Multiple records of the same type may appear (one per
        node within the slice).

        Args:
            record: The triggering record.

        Returns:
            Dictionary mapping record type names to lists of matching records.
        """
        await self._ensure_authenticated()

        if not record.patient:
            # Records without a patient violate ``validate_record_level``;
            # surface this loudly instead of returning an empty context silently.
            logger.warning(
                f"Record {record.id} ({record.record_type.name if record.record_type else '?'}) "
                f"has no patient — context is empty"
            )
            return {}

        try:
            records = await self.clarinet_client.find_records(
                patient_id=record.patient.id, limit=1000
            )
        except Exception as e:
            logger.error(f"Error getting record context: {e}")
            return {}

        return self._build_context_from_records(records, record)

    # ── Flow execution ────────────────────────────────────────────────────

    async def _evaluate_and_run_condition(
        self,
        condition: FlowCondition,
        context: dict[str, list[RecordRead]],
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
        self,
        flow: FlowRecord,
        record: RecordRead,
        context: dict[str, list[RecordRead]],
    ) -> None:
        """Execute a flow for a specific record.

        Args:
            flow: The flow definition to execute.
            record: The triggering record.
            context: Tree-filtered map of record type names to record lists.
        """
        # Ensure trigger is in context list (defensive — tree filter normally
        # already includes it). Clone the per-type list before mutating: the
        # outer dict was shallow-copied by ``_dispatch_flows`` per flow, but the
        # inner lists are shared and must not leak appends across flows.
        trigger_records = list(context.get(flow.record_name, []))
        if not any(r.id == record.id for r in trigger_records):
            trigger_records.append(record)
            trigger_records.sort(key=lambda x: x.id or 0)
        context[flow.record_name] = trigger_records
        context[_SELF] = [record]

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
                    await action_handlers.create_record(self, action, ctx)
                case UpdateRecordAction() if ctx.record is not None:
                    await action_handlers.update_record(self, action, ctx)
                case InvalidateRecordsAction():
                    await action_handlers.invalidate_records(self, action, ctx)
                case CallFunctionAction():
                    await action_handlers.call_function(self, action, ctx)
                case PipelineAction() if ctx.file_name is None:
                    await action_handlers.dispatch_pipeline(action, ctx)
                case _:
                    logger.warning(f"Unsupported action type for context: {action.type}")
        except Exception as e:
            if action_handlers._is_expected_conflict(e):
                logger.warning(f"Expected conflict in action {action.type}: {e}")
            else:
                logger.error(f"Error executing action {action.type}: {e}")
