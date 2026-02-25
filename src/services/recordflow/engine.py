"""
RecordFlowEngine for executing record flow definitions.

This module provides the RecordFlowEngine class that monitors record status
changes and executes registered flows when their conditions are met.
"""

from typing import TYPE_CHECKING, Any

from src.utils.logger import logger

from .flow_record import FlowRecord

if TYPE_CHECKING:
    from collections.abc import Callable

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

    def register_flow(self, flow: FlowRecord) -> None:
        """Register a flow definition.

        Args:
            flow: The FlowRecord to register.

        Raises:
            ValueError: If the flow definition is invalid.
        """
        flow.validate()

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
        self, action: dict[str, Any], record: RecordRead, context: dict[str, RecordRead]
    ) -> None:
        """Execute a single action.

        Args:
            action: The action dictionary with type and parameters.
            record: The triggering record.
            context: Dictionary of related records.
        """
        action_type = action.get("type")

        try:
            if action_type == "create_record":
                await self._create_record(action, record, context)
            elif action_type == "update_record":
                await self._update_record(action, record, context)
            elif action_type == "invalidate_records":
                await self._invalidate_records(action, record, context)
            elif action_type == "call_function":
                await self._call_function(action, record, context)
            else:
                logger.warning(f"Unknown action type: {action_type}")
        except Exception as e:
            logger.error(f"Error executing action {action_type}: {e}")

    async def _create_record(
        self,
        action: dict[str, Any],
        record: RecordRead,
        context: dict[str, RecordRead],  # noqa: ARG002 - kept for API consistency
    ) -> None:
        """Create a new record.

        Args:
            action: The action dictionary containing record_type_name and params.
            record: The triggering record.
            context: Dictionary of related records.
        """
        from src.models import RecordCreate

        record_type_name = action["record_type_name"]
        params = action.get("params", {})

        # Build create params from the triggering record
        create_params = {
            "record_type_name": record_type_name,
            "patient_id": record.patient.id,
            "study_uid": record.study.study_uid,
        }

        # Add series_uid if available and not overridden
        if record.series and "series_uid" not in params:
            create_params["series_uid"] = record.series.series_uid

        # Add any custom params
        if "series_uid" in params:
            create_params["series_uid"] = params["series_uid"]
        if "user_id" in params:
            create_params["user_id"] = params["user_id"]
        if "context_info" in params:
            create_params["context_info"] = params["context_info"]
        else:
            create_params["context_info"] = (
                f"Created by flow from record {record.record_type.name} (id={record.id})"
            )

        try:
            record_create = RecordCreate(**create_params)
            result = await self.clarinet_client.create_record(record_create)
            logger.info(
                f"Created record '{record_type_name}' (id={result.id}) "
                f"for study {create_params['study_uid']}"
            )
        except Exception as e:
            logger.error(f"Failed to create record '{record_type_name}': {e}")

    async def _update_record(
        self,
        action: dict[str, Any],
        record: RecordRead,  # noqa: ARG002 - kept for API consistency
        context: dict[str, RecordRead],
    ) -> None:
        """Update an existing record.

        Args:
            action: The action dictionary containing record_name and params.
            record: The triggering record.
            context: Dictionary of related records.
        """
        from src.models import RecordStatus

        record_name = action["record_name"]
        params = action.get("params", {})

        if record_name not in context:
            logger.warning(f"Record '{record_name}' not found in context for update")
            return

        target_record = context[record_name]

        # Update record status if specified
        if "status" in params:
            try:
                status = params["status"]
                if isinstance(status, str):
                    status = RecordStatus(status)

                await self.clarinet_client.update_record_status(target_record.id, status)
                logger.info(
                    f"Updated record '{record_name}' (id={target_record.id}) status to {status}"
                )
            except Exception as e:
                logger.error(f"Failed to update record status: {e}")

    async def _invalidate_records(
        self,
        action: dict[str, Any],
        record: RecordRead,
        context: dict[str, RecordRead],  # noqa: ARG002 - kept for API consistency
    ) -> None:
        """Invalidate records of specified types related to the source record.

        Searches by patient_id (broadest scope) to find ALL records of
        target types, covering all hierarchy levels. A series-level change
        can invalidate patient-level records and vice versa.

        Args:
            action: The action dictionary containing record_type_names, mode, and callback.
            record: The triggering record.
            context: Dictionary of related records (not used; we query directly).
        """
        record_type_names: list[str] = action["record_type_names"]
        mode: str = action.get("mode", "hard")
        callback: Callable | None = action.get("callback")

        for target_type_name in record_type_names:
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
                            mode=mode,
                            source_record_id=record.id,
                        )
                        logger.info(
                            f"Invalidated record '{target_type_name}' (id={target.id}) "
                            f"mode='{mode}', triggered by record {record.id}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to invalidate record '{target_type_name}' "
                            f"(id={target.id}): {e}"
                        )

                    # Call project-level callback if provided
                    if callback is not None:
                        try:
                            import asyncio

                            result = callback(
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
        self, action: dict[str, Any], record: RecordRead, context: dict[str, RecordRead]
    ) -> None:
        """Call a custom function.

        Args:
            action: The action dictionary containing function, args, and kwargs.
            record: The triggering record.
            context: Dictionary of related records.
        """
        func: Callable = action["function"]
        args: tuple = action.get("args", ())
        kwargs: dict = action.get("kwargs", {}).copy()

        # Add record, context, and client to kwargs if not present
        if "record" not in kwargs:
            kwargs["record"] = record
        if "context" not in kwargs:
            kwargs["context"] = context
        if "client" not in kwargs:
            kwargs["client"] = self.clarinet_client

        try:
            # Handle both sync and async functions
            import asyncio

            result = func(*args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
        except Exception as e:
            logger.error(f"Error calling function {func.__name__}: {e}")
