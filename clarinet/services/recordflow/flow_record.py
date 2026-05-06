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

from typing import TYPE_CHECKING, Any

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

    from clarinet.models import RecordStatus


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
        self.file_change_trigger: bool = False
        self.entity_trigger: str | None = entity_trigger
        self.conditions: list[FlowCondition] = []
        self.actions: list[FlowAction] = []
        self._current_condition: FlowCondition | None = None
        self._match_field: FlowResult | None = None
        self._match_guard: ComparisonResult | None = None
        self._match_on_missing: str = "skip"
        self._match_group_id: int = 0
        self._match_group_counter: int = 0
        self._match_case_count: int = 0

    @property
    def data(self) -> FlowResult:
        """Get a FlowResult object for this record's data fields (single)."""
        return FlowResult(self.record_name)

    @property
    def d(self) -> FlowResult:
        """Shorthand for data property."""
        return self.data

    def any(self) -> FlowResult:
        """Return a FlowResult for this record type with ``any``-strategy.

        Use when the context may contain multiple records of this type and
        the comparison should succeed if any one of them matches.

        Example:
            record('first-check').any().d.is_good == True
        """
        return FlowResult(self.record_name, strategy="any")

    def all(self) -> FlowResult:
        """Return a FlowResult for this record type with ``all``-strategy.

        Use when every record of this type in context must match. An empty
        list yields ``False`` (no records ⇒ no match).

        Example:
            record('segment').all().d.status == "approved"
        """
        return FlowResult(self.record_name, strategy="all")

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

    def on_file_change(self) -> FlowRecord:
        """Trigger this flow when record file checksums change.

        This trigger fires when file checksums are recomputed via
        POST /records/{id}/check-files and changes are detected.

        Returns:
            Self for method chaining.
        """
        self.file_change_trigger = True
        return self

    def on_created(self) -> FlowRecord:
        """Trigger this flow when the entity is created.

        Only valid for entity flows created via series(), study(), or patient()
        factory functions. Marks the flow as triggered on entity creation.

        Returns:
            Self for method chaining.
        """
        return self

    def on_finished(self) -> FlowRecord:
        """Shorthand for on_status('finished')."""
        return self.on_status("finished")

    def on_creation(self) -> FlowRecord:
        """Alias for on_created()."""
        return self.on_created()

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

    def if_record(self, *conditions: ComparisonResult, on_missing: str = "skip") -> FlowRecord:
        """Add conditions on the triggering record's own data (AND semantics).

        Combines multiple conditions with AND logic. Typically used with
        the Field proxy for concise self-referential conditions.

        Args:
            *conditions: One or more comparisons (e.g. ``F.field == val``).
            on_missing: How to handle missing/None fields during evaluation.
                ``"skip"`` — treat condition as False (default).
                ``"raise"`` — propagate the error.

        Returns:
            Self for method chaining.

        Raises:
            ValueError: If no conditions are provided.

        Example:
            F = Field()
            record("first_check")
                .on_status("finished")
                .if_record(F.is_good == True, F.study_type == "CT")
                .add_record("segment_CT")
        """
        if not conditions:
            raise ValueError("if_record() requires at least one condition")

        combined: ComparisonResult = conditions[0]
        for cond in conditions[1:]:
            combined = LogicalComparison(combined, cond, "and")

        self._current_condition = FlowCondition(combined, on_missing=on_missing)
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

    def match(self, field: FlowResult) -> FlowRecord:
        """Start a match block on a field for pattern matching.

        If preceded by ``if_record()`` without actions, the guard condition
        is absorbed and combined with each subsequent ``case()`` via AND.

        Args:
            field: The field to match on (e.g. ``F.study_type``).

        Returns:
            Self for method chaining.
        """
        self._match_field = field
        self._match_group_counter += 1
        self._match_group_id = self._match_group_counter

        # Absorb preceding if_record() guard if it has no actions
        if self._current_condition and not self._current_condition.actions:
            self._match_guard = self._current_condition.condition
            self._match_on_missing = self._current_condition.on_missing
            self.conditions.remove(self._current_condition)
            self._current_condition = None

        return self

    def case(self, value: Any) -> FlowRecord:
        """Add a case branch to the current match block.

        Creates a new ``FlowCondition`` with ``match_field == value``,
        optionally combined with the guard from a preceding ``if_record()``.

        Args:
            value: The value to compare the match field against.

        Returns:
            Self for method chaining.

        Raises:
            ValueError: If called without a preceding ``match()``.
        """
        if self._match_field is None:
            raise ValueError("case() must be called after match()")

        case_condition: ComparisonResult = self._match_field == value
        if self._match_guard is not None:
            case_condition = LogicalComparison(self._match_guard, case_condition, "and")

        self._current_condition = FlowCondition(
            case_condition,
            on_missing=self._match_on_missing,
            match_group=self._match_group_id,
        )
        self.conditions.append(self._current_condition)
        self._match_case_count += 1
        return self

    def default(self) -> FlowRecord:
        """Add a default branch to the current match block.

        The default branch fires only when no preceding ``case()`` in the
        same match group matched.  If a guard was set via ``if_record()``,
        the default also carries it — so when the guard is False, the
        default does **not** fire.

        Returns:
            Self for method chaining.

        Raises:
            ValueError: If called without a preceding ``match()``.
        """
        if self._match_field is None:
            raise ValueError("default() must be called after match()")

        self._current_condition = FlowCondition(
            self._match_guard,  # None when no guard
            is_else=True,
            on_missing=self._match_on_missing,
            match_group=self._match_group_id,
        )
        self.conditions.append(self._current_condition)
        return self

    def add_record(self, record_type_name: str, **kwargs: object) -> FlowRecord:
        """Add a record creation action.

        Args:
            record_type_name: The name of the record type to create.
            **kwargs: Additional parameters for record creation
                     (e.g., user_id, context_info, series_uid, inherit_user).

        Returns:
            Self for method chaining.
        """
        action = CreateRecordAction(
            record_type_name=record_type_name,
            series_uid=kwargs.get("series_uid"),  # type: ignore[arg-type]
            user_id=kwargs.get("user_id"),  # type: ignore[arg-type]
            parent_record_id=kwargs.get("parent_record_id"),  # type: ignore[arg-type]
            inherit_user=kwargs.get("inherit_user", False),  # type: ignore[arg-type]
            context_info=kwargs.get("context_info"),  # type: ignore[arg-type]
        )

        if self._current_condition:
            self._current_condition.add_action(action)
        else:
            self.actions.append(action)

        return self

    def create_record(self, *record_type_names: str, inherit_user: bool = False) -> FlowRecord:
        """Create records for one or more record types.

        Convenience wrapper around add_record() supporting multiple names.

        Args:
            *record_type_names: Names of record types to create.
            inherit_user: If True, inherit user_id from triggering record.

        Returns:
            Self for method chaining.
        """
        for name in record_type_names:
            self.add_record(name, inherit_user=inherit_user)
        return self

    def update_record(self, record_name: str, **kwargs: object) -> FlowRecord:
        """Add a record update action.

        Args:
            record_name: The name of the record type to update.
            **kwargs: Parameters to update (``status``, ``strategy``).
                ``strategy='single'`` (default): skip with error log if
                context contains 0 or >1 records of this type.
                ``strategy='all'``: apply update to every matching record.

        Returns:
            Self for method chaining.
        """
        action = UpdateRecordAction(
            record_name=record_name,
            status=kwargs.get("status"),  # type: ignore[arg-type]
            strategy=kwargs.get("strategy", "single"),  # type: ignore[arg-type]
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

    def invalidate_all_records(
        self,
        *record_type_names: str,
        mode: str = "hard",
        callback: Callable | None = None,
    ) -> FlowRecord:
        """Alias for invalidate_records()."""
        return self.invalidate_records(*record_type_names, mode=mode, callback=callback)

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

    def do_task(self, task_func: Any, **extra_payload: object) -> FlowRecord:
        """Add a task dispatch action.

        Creates an auto-pipeline from a @pipeline_task()-decorated function
        and dispatches it to the task queue.

        Args:
            task_func: A @pipeline_task()-decorated function (AsyncTaskiqDecoratedTask).
            **extra_payload: Additional key-value data for the pipeline message.

        Returns:
            Self for method chaining.
        """
        from clarinet.services.pipeline import Pipeline, get_pipeline

        func_name = task_func.task_name.rsplit(":", 1)[-1]
        pipeline_name = f"_task:{func_name}"
        if get_pipeline(pipeline_name) is None:
            # Step picks up the task's bound queue (_pipeline_queue) by default.
            Pipeline(pipeline_name).step(task_func)

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
            or self.file_change_trigger
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

        # match() without any case() is invalid
        if self._match_field is not None and self._match_case_count == 0:
            raise ValueError(f"match() in flow '{self.record_name}' has no case() branches")

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
            if self.file_change_trigger:
                parts.append(".on_file_change()")
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
