"""
RecordFlowEngine for executing record flow definitions.

This module provides the RecordFlowEngine class that monitors record status
changes and executes registered flows when their conditions are met.
"""

from typing import TYPE_CHECKING

from src.utils.logger import logger

from .flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    FlowAction,
    InvalidateRecordsAction,
    UpdateRecordAction,
)
from .flow_record import FlowRecord

if TYPE_CHECKING:
    from src.client import ClarinetClient
    from src.models import RecordRead, RecordStatus


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

    def register_flow(self, flow: FlowRecord) -> None:
        """Register a flow definition.

        Args:
            flow: The FlowRecord to register.

        Raises:
            ValueError: If the flow definition is invalid.
        """
        flow.validate()

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
        elif flow.status_trigger:
            logger.info(
                f"Registered flow for record type '{flow.record_name}' "
                f"on status '{flow.status_trigger}'"
            )
        else:
            logger.info(
                f"Registered flow for record type '{flow.record_name}' on any status change"
            )

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
        record_type_name = record.record_type.name

        if record_type_name not in self.flows:
            logger.debug(f"No flows registered for record type '{record_type_name}'")
            return

        logger.debug(f"Processing flows for record {record.id} ({record_type_name})")

        # Get all records in the same study/series for context
        record_context = await self._get_record_context(record)

        # Execute relevant flows (skip data_update_trigger flows)
        for flow in self.flows[record_type_name]:
            if flow.data_update_trigger:
                continue
            # Execute if status matches or if no specific status trigger is set
            current_status = (
                record.status.value if hasattr(record.status, "value") else record.status
            )
            if flow.status_trigger is None or flow.status_trigger == current_status:
                logger.info(
                    f"Executing flow for record '{record_type_name}' "
                    f"(id={record.id}) on status '{current_status}'"
                )
                await self._execute_flow(flow, record, record_context)

    async def handle_record_data_update(self, record: RecordRead) -> None:
        """Handle a data update on a finished record.

        Only executes flows with data_update_trigger=True. This is called
        when record data is modified via PATCH /records/{id}/data.

        Args:
            record: The record whose data was updated.
        """
        record_type_name = record.record_type.name

        if record_type_name not in self.flows:
            logger.debug(f"No flows registered for record type '{record_type_name}'")
            return

        logger.debug(f"Processing data update flows for record {record.id} ({record_type_name})")

        record_context = await self._get_record_context(record)

        for flow in self.flows[record_type_name]:
            if flow.data_update_trigger:
                logger.info(
                    f"Executing data update flow for record '{record_type_name}' (id={record.id})"
                )
                await self._execute_flow(flow, record, record_context)

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

        for flow in self.entity_flows[entity_type]:
            for action in flow.actions:
                await self._execute_entity_action(action, patient_id, study_uid, series_uid)

    async def _execute_entity_action(
        self,
        action: FlowAction,
        patient_id: str,
        study_uid: str | None,
        series_uid: str | None,
    ) -> None:
        """Execute a single action for an entity creation flow.

        Args:
            action: The action model instance.
            patient_id: The patient ID.
            study_uid: The study UID (if available).
            series_uid: The series UID (if available).
        """
        try:
            match action:
                case CreateRecordAction():
                    await self._create_entity_record(action, patient_id, study_uid, series_uid)
                case CallFunctionAction():
                    await self._call_entity_function(action, patient_id, study_uid, series_uid)
                case _:
                    logger.warning(f"Unsupported entity action type: {action.type}")
        except Exception as e:
            logger.error(f"Error executing entity action: {e}")

    async def _create_entity_record(
        self,
        action: CreateRecordAction,
        patient_id: str,
        study_uid: str | None,
        series_uid: str | None,
    ) -> None:
        """Create a record from an entity creation flow.

        Args:
            action: The CreateRecordAction with record details.
            patient_id: The patient ID.
            study_uid: The study UID (if available).
            series_uid: The series UID (if available).
        """
        from src.models import RecordCreate

        try:
            record_create = RecordCreate(
                record_type_name=action.record_type_name,
                patient_id=patient_id,
                study_uid=study_uid,
                series_uid=action.series_uid or series_uid,
                user_id=action.user_id,
                context_info=action.context_info
                or f"Created by entity flow on {series_uid or study_uid or patient_id} creation",
            )
            result = await self.clarinet_client.create_record(record_create)
            logger.info(
                f"Created record '{action.record_type_name}' (id={result.id}) from entity flow"
            )
        except Exception as e:
            logger.error(
                f"Failed to create record '{action.record_type_name}' from entity flow: {e}"
            )

    async def _call_entity_function(
        self,
        action: CallFunctionAction,
        patient_id: str,
        study_uid: str | None,
        series_uid: str | None,
    ) -> None:
        """Call a custom function from an entity creation flow.

        Args:
            action: The CallFunctionAction with function, args, and kwargs.
            patient_id: The patient ID.
            study_uid: The study UID (if available).
            series_uid: The series UID (if available).
        """
        kwargs = {
            "patient_id": patient_id,
            "study_uid": study_uid,
            "series_uid": series_uid,
            "client": self.clarinet_client,
        } | action.extra_kwargs

        try:
            import asyncio

            result = action.function(*action.args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Error calling entity function {action.function.__name__}: {e}")

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
        context: dict[str, RecordRead] = {}

        try:
            # Get patient-level records (broadest scope)
            if record.patient:
                patient_records = await self.clarinet_client.find_records(
                    patient_id=record.patient.id, limit=1000
                )
                for r in patient_records:
                    if r.record_type and r.record_type.name:
                        record_name = r.record_type.name
                        if record_name not in context or (r.id and r.id > context[record_name].id):
                            context[record_name] = r

            # Study-level records override patient-level for same type
            if record.study:
                study_records = await self.clarinet_client.find_records(
                    study_uid=record.study.study_uid, limit=1000
                )
                for r in study_records:
                    if r.record_type and r.record_type.name:
                        record_name = r.record_type.name
                        if record_name not in context or (r.id and r.id > context[record_name].id):
                            context[record_name] = r

            # Series-level records override study-level for same type
            if record.series:
                series_records = await self.clarinet_client.find_records(
                    series_uid=record.series.series_uid, limit=1000
                )
                for r in series_records:
                    if r.record_type and r.record_type.name:
                        record_name = r.record_type.name
                        context[record_name] = r

        except Exception as e:
            logger.error(f"Error getting record context: {e}")

        return context

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

        # Execute unconditional actions
        for action in flow.actions:
            await self._execute_action(action, record, context)

        # Evaluate and execute conditional actions
        previous_condition_met = False
        for condition in flow.conditions:
            if condition.is_else:
                # Execute else block only if previous condition was false
                if not previous_condition_met:
                    for action in condition.actions:
                        await self._execute_action(action, record, context)
                break
            else:
                # Evaluate condition
                try:
                    condition_met = condition.evaluate(context)
                    if condition_met:
                        for action in condition.actions:
                            await self._execute_action(action, record, context)
                        previous_condition_met = True
                    else:
                        previous_condition_met = False
                except Exception as e:
                    logger.error(f"Error evaluating condition: {e}")
                    previous_condition_met = False

    async def _execute_action(
        self, action: FlowAction, record: RecordRead, context: dict[str, RecordRead]
    ) -> None:
        """Execute a single action.

        Args:
            action: The action model instance.
            record: The triggering record.
            context: Dictionary of related records.
        """
        try:
            match action:
                case CreateRecordAction():
                    await self._create_record(action, record, context)
                case UpdateRecordAction():
                    await self._update_record(action, record, context)
                case InvalidateRecordsAction():
                    await self._invalidate_records(action, record, context)
                case CallFunctionAction():
                    await self._call_function(action, record, context)
                case _:
                    logger.warning(f"Unknown action type: {action.type}")
        except Exception as e:
            logger.error(f"Error executing action {action.type}: {e}")

    async def _create_record(
        self,
        action: CreateRecordAction,
        record: RecordRead,
        context: dict[str, RecordRead],  # noqa: ARG002 - kept for API consistency
    ) -> None:
        """Create a new record.

        Args:
            action: The CreateRecordAction with record details.
            record: The triggering record.
            context: Dictionary of related records.
        """
        from src.models import RecordCreate

        series_uid = action.series_uid or (record.series.series_uid if record.series else None)

        try:
            record_create = RecordCreate(
                record_type_name=action.record_type_name,
                patient_id=record.patient.id,
                study_uid=record.study.study_uid,
                series_uid=series_uid,
                user_id=action.user_id,
                context_info=action.context_info
                or f"Created by flow from record {record.record_type.name} (id={record.id})",
            )
            result = await self.clarinet_client.create_record(record_create)
            logger.info(
                f"Created record '{action.record_type_name}' (id={result.id}) "
                f"for study {record.study.study_uid}"
            )
        except Exception as e:
            logger.error(f"Failed to create record '{action.record_type_name}': {e}")

    async def _update_record(
        self,
        action: UpdateRecordAction,
        record: RecordRead,  # noqa: ARG002 - kept for API consistency
        context: dict[str, RecordRead],
    ) -> None:
        """Update an existing record.

        Args:
            action: The UpdateRecordAction with target record name and status.
            record: The triggering record.
            context: Dictionary of related records.
        """
        from src.models import RecordStatus

        if action.record_name not in context:
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

    async def _invalidate_records(
        self,
        action: InvalidateRecordsAction,
        record: RecordRead,
        context: dict[str, RecordRead],  # noqa: ARG002 - kept for API consistency
    ) -> None:
        """Invalidate records of specified types related to the source record.

        Searches by patient_id (broadest scope) to find ALL records of
        target types, covering all hierarchy levels. A series-level change
        can invalidate patient-level records and vice versa.

        Args:
            action: The InvalidateRecordsAction with target types, mode, and callback.
            record: The triggering record.
            context: Dictionary of related records (not used; we query directly).
        """
        for target_type_name in action.record_type_names:
            try:
                # Find ALL records of target type for the same patient
                target_records = await self.clarinet_client.find_records(
                    patient_id=record.patient.id,
                    record_type_name=target_type_name,
                    limit=1000,
                )

                for target in target_records:
                    # Skip the source record itself
                    if target.id == record.id:
                        continue

                    try:
                        await self.clarinet_client.invalidate_record(
                            record_id=target.id,
                            mode=action.mode,
                            source_record_id=record.id,
                        )
                        logger.info(
                            f"Invalidated record '{target_type_name}' (id={target.id}) "
                            f"mode='{action.mode}', triggered by record {record.id}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to invalidate record '{target_type_name}' "
                            f"(id={target.id}): {e}"
                        )

                    # Call project-level callback if provided
                    if action.callback is not None:
                        try:
                            import asyncio

                            result = action.callback(
                                record=target,
                                source_record=record,
                                client=self.clarinet_client,
                            )
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(
                                f"Error in invalidation callback for record {target.id}: {e}"
                            )

            except Exception as e:
                logger.error(
                    f"Failed to find records of type '{target_type_name}' "
                    f"for patient {record.patient.id}: {e}"
                )

    async def _call_function(
        self, action: CallFunctionAction, record: RecordRead, context: dict[str, RecordRead]
    ) -> None:
        """Call a custom function.

        Args:
            action: The CallFunctionAction with function, args, and kwargs.
            record: The triggering record.
            context: Dictionary of related records.
        """
        kwargs = {
            "record": record,
            "context": context,
            "client": self.clarinet_client,
        } | action.extra_kwargs

        try:
            # Handle both sync and async functions
            import asyncio

            result = action.function(*action.args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Error calling function {action.function.__name__}: {e}")
