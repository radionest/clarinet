"""
FlowRecord class implementing the DSL for record flow definitions.

This module provides the FlowRecord class and the record() factory function
for creating declarative flow definitions.

Example usage:
    record('doctor_report')
        .on_status('finished')
        .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
        .or_(record('doctor_report').data.BIRADS_L != record('ai_report').data.BIRADS_L)
        .add_record('confirm_birads')

    # Invalidate dependent records when data is updated
    record('master_model')
        .on_data_update()
        .invalidate_records('child_analysis', mode='hard')

    # Trigger on entity creation
    series().on_created().add_record('series_markup')
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .flow_action import (
    CallFunctionAction,
    CreateRecordAction,
    FlowAction,
    InvalidateRecordsAction,
    PipelineAction,
    UpdateRecordAction,
)
from .flow_condition import FlowCondition
from .flow_result import ComparisonResult, FlowResult, LogicalComparison

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.models import RecordStatus


# Global registry for loaded flow records
RECORD_REGISTRY: list[FlowRecord] = []

# Global registry for entity creation flows
ENTITY_REGISTRY: list[FlowRecord] = []


class FlowRecord:
    """
    Represents a record type in a flow definition with DSL methods.

    This class provides a chainable API for defining workflows that trigger
    when a record of a specific type changes status.

    Example:
        record('doctor_report')
            .on_status('finished')
            .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
            .add_record('confirm_birads')
    """

    def __init__(self, record_name: str, *, entity_trigger: str | None = None):
        self.record_name = record_name
        self.status_trigger: str | None = None
        self.data_update_trigger: bool = False
        self.entity_trigger: str | None = entity_trigger
        self.conditions: list[FlowCondition] = []
        self.actions: list[FlowAction] = []
        self._current_condition: FlowCondition | None = None

    @property
    def data(self) -> FlowResult:
        """Get a FlowResult object for this record's data fields."""
        return FlowResult(self.record_name)

    @property
    def d(self) -> FlowResult:
        """Shorthand for data property."""
        return self.data

    def on_status(self, status: str | RecordStatus) -> FlowRecord:
        """Define which status change triggers this flow.

        Args:
            status: The status value that triggers this flow.
                   Can be a string or RecordStatus enum value.

        Returns:
            Self for method chaining.
        """
        if hasattr(status, "value"):
            self.status_trigger = status.value
        else:
            self.status_trigger = str(status)
        return self

    def on_data_update(self) -> FlowRecord:
        """Trigger this flow when a finished record's data is updated.

        This trigger is separate from on_status() and is only fired when
        record data is modified via PATCH /records/{id}/data.

        Returns:
            Self for method chaining.
        """
        self.data_update_trigger = True
        return self

    def on_created(self) -> FlowRecord:
        """Trigger this flow when the entity is created.

        Only valid for entity flows created via series(), study(), or patient()
        factory functions. Marks the flow as triggered on entity creation.

        Returns:
            Self for method chaining.
        """
        return self

    def if_(self, condition: ComparisonResult) -> FlowRecord:
        """Start a new condition block.

        Args:
            condition: The condition to evaluate.

        Returns:
            Self for method chaining.
        """
        if self._current_condition and not self._current_condition.actions:
            # Previous condition has no actions, this is likely chained conditions
            self._current_condition.add_condition(condition)
        else:
            # Start a new condition
            self._current_condition = FlowCondition(condition)
            self.conditions.append(self._current_condition)
        return self

    def or_(self, condition: ComparisonResult) -> FlowRecord:
        """Add an OR condition to the current condition block.

        Args:
            condition: The condition to OR with the current condition.

        Returns:
            Self for method chaining.

        Raises:
            ValueError: If called without a preceding if_() call.
        """
        if not self._current_condition or not self._current_condition.condition:
            raise ValueError("or_() must be called after if_()")

        # Combine with OR logic
        combined = LogicalComparison(self._current_condition.condition, condition, "or")
        self._current_condition.condition = combined
        return self

    def and_(self, condition: ComparisonResult) -> FlowRecord:
        """Add an AND condition to the current condition block.

        Args:
            condition: The condition to AND with the current condition.

        Returns:
            Self for method chaining.

        Raises:
            ValueError: If called without a preceding if_() call.
        """
        if not self._current_condition or not self._current_condition.condition:
            raise ValueError("and_() must be called after if_()")

        # Combine with AND logic
        combined = LogicalComparison(self._current_condition.condition, condition, "and")
        self._current_condition.condition = combined
        return self

    def add_record(self, record_type_name: str, **kwargs: object) -> FlowRecord:
        """Add a record creation action.

        Args:
            record_type_name: The name of the record type to create.
            **kwargs: Additional parameters for record creation
                     (e.g., user_id, context_info, series_uid).

        Returns:
            Self for method chaining.
        """
        action = CreateRecordAction(
            record_type_name=record_type_name,
            series_uid=kwargs.get("series_uid"),  # type: ignore[arg-type]
            user_id=kwargs.get("user_id"),  # type: ignore[arg-type]
            context_info=kwargs.get("context_info"),  # type: ignore[arg-type]
        )

        if self._current_condition:
            self._current_condition.add_action(action)
        else:
            self.actions.append(action)

        return self

    def update_record(self, record_name: str, **kwargs: object) -> FlowRecord:
        """Add a record update action.

        Args:
            record_name: The name of the record type to update.
            **kwargs: Parameters to update (e.g., status='finished').

        Returns:
            Self for method chaining.
        """
        action = UpdateRecordAction(
            record_name=record_name,
            status=kwargs.get("status"),  # type: ignore[arg-type]
        )

        if self._current_condition:
            self._current_condition.add_action(action)
        else:
            self.actions.append(action)

        return self

    def call(self, func: Callable, *args: object, **kwargs: object) -> FlowRecord:
        """Add a custom function call action.

        The function will be called with the following keyword arguments
        (if not already provided):
        - record: The triggering record
        - context: Dictionary of related records
        - client: The ClarinetClient instance

        Args:
            func: The function to call.
            *args: Positional arguments to pass to the function.
            **kwargs: Keyword arguments to pass to the function.

        Returns:
            Self for method chaining.
        """
        action = CallFunctionAction(
            function=func,
            args=args,
            extra_kwargs=dict(kwargs),
        )

        if self._current_condition:
            self._current_condition.add_action(action)
        else:
            self.actions.append(action)

        return self

    def invalidate_records(
        self,
        *record_type_names: str,
        mode: str = "hard",
        callback: Callable | None = None,
    ) -> FlowRecord:
        """Add an invalidation action for records of specified types.

        Searches by patient_id (broadest scope), covering all hierarchy levels.
        A series-level change can invalidate patient-level records and vice versa.

        Args:
            record_type_names: Names of record types to invalidate.
            mode: "hard" resets status to pending (keeps user_id).
                  "soft" only appends reason to context_info without status change.
            callback: Optional project-level callback(record, source_record, client)
                      for custom behavior (e.g. updating context_info).

        Returns:
            Self for method chaining.
        """
        action = InvalidateRecordsAction(
            record_type_names=list(record_type_names),
            mode=mode,
            callback=callback,
        )

        if self._current_condition:
            self._current_condition.add_action(action)
        else:
            self.actions.append(action)

        return self

    def pipeline(self, pipeline_name: str, **extra_payload: object) -> FlowRecord:
        """Add a pipeline dispatch action.

        Sends a message to the named pipeline for distributed execution.
        The pipeline message is populated from the triggering record's context.

        Args:
            pipeline_name: Name of the registered pipeline to run.
            **extra_payload: Additional key-value data for the pipeline message.

        Returns:
            Self for method chaining.
        """
        action = PipelineAction(
            pipeline_name=pipeline_name,
            extra_payload=dict(extra_payload),
        )

        if self._current_condition:
            self._current_condition.add_action(action)
        else:
            self.actions.append(action)

        return self

    def else_(self) -> FlowRecord:
        """Start an else block for the current condition.

        Returns:
            Self for method chaining.

        Raises:
            ValueError: If called without a preceding if_() call.
        """
        if not self._current_condition:
            raise ValueError("else_() must be called after if_()")

        # Create a new condition that's marked as else
        self._current_condition = FlowCondition(None, is_else=True)
        self.conditions.append(self._current_condition)
        return self

    def is_active_flow(self) -> bool:
        """Check if this FlowRecord defines an actual flow (not just a data reference).

        Returns True if the flow has triggers, actions, or conditions.
        Returns False for FlowRecord instances created only for data field
        references (e.g. record('type').data.field in comparisons).
        """
        return bool(
            self.status_trigger
            or self.data_update_trigger
            or self.entity_trigger
            or self.actions
            or self.conditions
        )

    def validate(self) -> bool:
        """Validate the flow definition.

        Returns:
            True if valid.

        Raises:
            ValueError: If the flow definition is invalid.
        """
        # Status trigger is optional - None means trigger on any status change

        # Check that all conditions have actions
        for condition in self.conditions:
            if not condition.actions and not condition.is_else:
                raise ValueError(f"Condition in flow '{self.record_name}' has no actions")

        return True

    def __repr__(self) -> str:
        if self.entity_trigger:
            parts = [f"{self.entity_trigger}().on_created()"]
        else:
            parts = [f"FlowRecord('{self.record_name}')"]
            if self.status_trigger:
                parts.append(f".on_status('{self.status_trigger}')")
            if self.data_update_trigger:
                parts.append(".on_data_update()")
        return "".join(parts)


def record(record_name: str) -> FlowRecord:
    """
    Create a new FlowRecord instance for the given record type name.

    Each call creates a new FlowRecord and adds it to the global registry.
    This allows defining multiple independent flows for the same record type
    (e.g. one triggered on status change, another on data update).

    FlowRecord instances created only for data references (e.g.
    ``record('type').data.field`` in comparisons) will be filtered out
    by the loader via ``is_active_flow()``.

    This function is the main entry point for creating flow definitions.

    Args:
        record_name: The name of the record type this flow applies to.

    Returns:
        A new FlowRecord instance for chaining DSL methods.

    Example:
        record('doctor_report')
            .on_status('finished')
            .if_(record('doctor_report').data.BIRADS_R != record('ai_report').data.BIRADS_R)
            .add_record('confirm_birads')

        record('master_model')
            .on_data_update()
            .invalidate_records('child_analysis', mode='hard')
    """
    new_record = FlowRecord(record_name)
    RECORD_REGISTRY.append(new_record)
    return new_record


def _entity_factory(entity_type: str) -> FlowRecord:
    """Create a FlowRecord for an entity creation trigger.

    Args:
        entity_type: The entity type ("series", "study", or "patient").

    Returns:
        A new FlowRecord with entity_trigger set.
    """
    flow = FlowRecord(entity_type, entity_trigger=entity_type)
    ENTITY_REGISTRY.append(flow)
    return flow


def series() -> FlowRecord:
    """Create a flow triggered when a new series is created.

    Returns:
        A new FlowRecord for chaining DSL methods.

    Example:
        series().on_created().add_record('series_markup')
    """
    return _entity_factory("series")


def study() -> FlowRecord:
    """Create a flow triggered when a new study is created.

    Returns:
        A new FlowRecord for chaining DSL methods.

    Example:
        study().on_created().add_record('study_review')
    """
    return _entity_factory("study")


def patient() -> FlowRecord:
    """Create a flow triggered when a new patient is created.

    Returns:
        A new FlowRecord for chaining DSL methods.

    Example:
        patient().on_created().add_record('patient_intake')
    """
    return _entity_factory("patient")
