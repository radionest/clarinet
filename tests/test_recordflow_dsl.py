"""Unit tests for RecordFlow DSL — expression tree, comparisons, and builder.

Pure logic tests, no I/O or database. Uses real Pydantic models as context.
"""

from datetime import UTC, datetime

import pytest

from src.models.base import DicomQueryLevel, RecordStatus
from src.models.patient import PatientBase
from src.models.record import RecordRead, RecordTypeBase
from src.models.study import StudyBase
from src.services.recordflow import (
    RECORD_REGISTRY,
    ConstantFlowResult,
    FieldComparison,
    FlowCondition,
    FlowRecord,
    FlowResult,
    LogicalComparison,
)
from src.services.recordflow.flow_record import record


def make_record_read(
    name: str,
    data: dict | None = None,
    record_id: int = 1,
    status: RecordStatus = RecordStatus.pending,
) -> RecordRead:
    """Create a RecordRead instance without DB for use as expression context."""
    return RecordRead(
        id=record_id,
        data=data,
        status=status,
        record_type_name=name,
        patient_id="PAT001",
        study_uid="1.2.3.4.5",
        created_at=datetime.now(UTC),
        changed_at=datetime.now(UTC),
        patient=PatientBase(id="PAT001", name="Test Patient"),
        study=StudyBase(
            study_uid="1.2.3.4.5",
            date=datetime.now(UTC).date(),
            patient_id="PAT001",
        ),
        series=None,
        record_type=RecordTypeBase(name=name, level=DicomQueryLevel.STUDY),
    )


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global record registry before each test."""
    RECORD_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()


# ─── FlowResult — expression tree ────────────────────────────────────────────


class TestFlowResult:
    """Tests for FlowResult field access and value resolution."""

    def test_single_field_access(self):
        """FlowResult._get_value extracts a value from record.data."""
        ctx = {"report": make_record_read("report", data={"score": 42})}
        result = FlowResult("report", ["score"])
        assert result._get_value(ctx) == 42

    def test_nested_field_access(self):
        """FlowResult navigates nested dicts via field_path."""
        ctx = {"report": make_record_read("report", data={"findings": {"tumor": "yes"}})}
        result = FlowResult("report", ["findings", "tumor"])
        assert result._get_value(ctx) == "yes"

    def test_missing_record_raises(self):
        """ValueError when record name is not in context."""
        ctx = {"other": make_record_read("other", data={})}
        result = FlowResult("missing_record", ["field"])
        with pytest.raises(ValueError, match="not found in context"):
            result._get_value(ctx)

    def test_getattr_builds_path(self):
        """Attribute access chains build field_path list."""
        fr = FlowResult("r")
        chained = fr.a.b.c
        assert chained.record_name == "r"
        assert chained.field_path == ["a", "b", "c"]

    def test_data_field_with_none_data(self):
        """Accessing a field on None data returns None via dict.get."""
        ctx = {"report": make_record_read("report", data=None)}
        result = FlowResult("report", ["anything"])
        with pytest.raises(ValueError, match="Cannot access field"):
            result._get_value(ctx)

    def test_constant_flow_result_returns_value(self):
        """ConstantFlowResult always returns its stored value."""
        const = ConstantFlowResult(99)
        assert const._get_value({}) == 99


# ─── Comparisons — operators + evaluation ─────────────────────────────────────


class TestComparisons:
    """Tests for comparison operators and their evaluation."""

    def test_eq_same_values(self):
        """== returns True for equal values."""
        ctx = {"r": make_record_read("r", data={"x": 10})}
        cmp = FlowResult("r", ["x"]) == FlowResult("r", ["x"])
        assert cmp.evaluate(ctx) is True

    def test_eq_different_values(self):
        """== returns False for different values."""
        ctx = {
            "a": make_record_read("a", data={"x": 10}),
            "b": make_record_read("b", data={"x": 20}),
        }
        cmp = FlowResult("a", ["x"]) == FlowResult("b", ["x"])
        assert cmp.evaluate(ctx) is False

    def test_comparison_with_constant(self):
        """FlowResult == literal wraps literal in ConstantFlowResult."""
        ctx = {"r": make_record_read("r", data={"val": 42})}
        cmp = FlowResult("r", ["val"]) == 42
        assert isinstance(cmp, FieldComparison)
        assert isinstance(cmp.right, ConstantFlowResult)
        assert cmp.evaluate(ctx) is True

    def test_logical_and(self):
        """LogicalComparison with 'and' requires both sides true."""
        ctx = {"r": make_record_read("r", data={"a": 1, "b": 2})}
        left = FlowResult("r", ["a"]) == 1
        right = FlowResult("r", ["b"]) == 2
        combined = LogicalComparison(left, right, "and")
        assert combined.evaluate(ctx) is True

        # One side false
        ctx2 = {"r": make_record_read("r", data={"a": 1, "b": 99})}
        assert combined.evaluate(ctx2) is False

    def test_logical_or(self):
        """LogicalComparison with 'or' requires at least one side true."""
        ctx = {"r": make_record_read("r", data={"a": 1, "b": 99})}
        left = FlowResult("r", ["a"]) == 1
        right = FlowResult("r", ["b"]) == 2
        combined = LogicalComparison(left, right, "or")
        assert combined.evaluate(ctx) is True

        # Both false
        ctx2 = {"r": make_record_read("r", data={"a": 0, "b": 99})}
        assert combined.evaluate(ctx2) is False

    @pytest.mark.parametrize(
        ("op_method", "left_val", "right_val", "expected"),
        [
            ("__eq__", 10, 10, True),
            ("__eq__", 10, 20, False),
            ("__ne__", 10, 20, True),
            ("__ne__", 10, 10, False),
            ("__lt__", 5, 10, True),
            ("__lt__", 10, 5, False),
            ("__le__", 10, 10, True),
            ("__le__", 11, 10, False),
            ("__gt__", 10, 5, True),
            ("__gt__", 5, 10, False),
            ("__ge__", 10, 10, True),
            ("__ge__", 9, 10, False),
        ],
        ids=[
            "eq-true",
            "eq-false",
            "ne-true",
            "ne-false",
            "lt-true",
            "lt-false",
            "le-true",
            "le-false",
            "gt-true",
            "gt-false",
            "ge-true",
            "ge-false",
        ],
    )
    def test_all_operators(self, op_method, left_val, right_val, expected):
        """All comparison operators produce correct FieldComparison results."""
        ctx = {
            "a": make_record_read("a", data={"v": left_val}),
            "b": make_record_read("b", data={"v": right_val}),
        }
        fr_left = FlowResult("a", ["v"])
        fr_right = FlowResult("b", ["v"])
        comparison = getattr(fr_left, op_method)(fr_right)
        assert isinstance(comparison, FieldComparison)
        assert comparison.evaluate(ctx) is expected


# ─── FlowRecord DSL — builder ────────────────────────────────────────────────


class TestFlowRecordDSL:
    """Tests for the FlowRecord builder DSL."""

    def test_record_factory_creates_and_registers(self):
        """record() creates a new FlowRecord and adds it to RECORD_REGISTRY."""
        fr = record("test_record_type")
        assert isinstance(fr, FlowRecord)
        assert fr.record_name == "test_record_type"
        assert fr in RECORD_REGISTRY

    def test_record_factory_returns_existing(self):
        """record() returns existing FlowRecord if name already registered."""
        fr1 = record("same_record_type")
        fr2 = record("same_record_type")
        assert fr1 is fr2

    def test_on_status_sets_trigger(self):
        """on_status() sets status_trigger string."""
        fr = FlowRecord("test_type")
        result = fr.on_status("finished")
        assert result is fr  # returns self for chaining
        assert fr.status_trigger == "finished"

    def test_on_status_with_enum(self):
        """on_status() accepts RecordStatus enum."""
        fr = FlowRecord("test_type")
        fr.on_status(RecordStatus.finished)
        assert fr.status_trigger == "finished"

    def test_if_or_and_chaining(self):
        """if_().or_().and_() builds nested LogicalComparison."""
        fr = FlowRecord("test_type")
        a = FlowResult("r", ["a"]) == 1
        b = FlowResult("r", ["b"]) == 2
        c = FlowResult("r", ["c"]) == 3

        fr.if_(a).or_(b).and_(c)

        assert len(fr.conditions) == 1
        condition = fr.conditions[0].condition
        # Structure: (a OR b) AND c
        assert isinstance(condition, LogicalComparison)
        assert condition.operator == "and"

    def test_or_without_if_raises(self):
        """or_() without preceding if_() raises ValueError."""
        fr = FlowRecord("test_type")
        with pytest.raises(ValueError, match="or_.*must be called after if_"):
            fr.or_(FlowResult("r", ["x"]) == 1)

    def test_and_without_if_raises(self):
        """and_() without preceding if_() raises ValueError."""
        fr = FlowRecord("test_type")
        with pytest.raises(ValueError, match="and_.*must be called after if_"):
            fr.and_(FlowResult("r", ["x"]) == 1)

    def test_else_without_if_raises(self):
        """else_() without preceding if_() raises ValueError."""
        fr = FlowRecord("test_type")
        with pytest.raises(ValueError, match="else_.*must be called after if_"):
            fr.else_()

    def test_add_record_unconditional(self):
        """add_record() without if_() adds to self.actions."""
        fr = FlowRecord("test_type")
        fr.add_record("new_type")
        assert len(fr.actions) == 1
        assert fr.actions[0]["type"] == "create_record"
        assert fr.actions[0]["record_type_name"] == "new_type"

    def test_add_record_conditional(self):
        """add_record() after if_() adds to condition.actions."""
        fr = FlowRecord("test_type")
        fr.if_(FlowResult("r", ["x"]) == 1).add_record("new_type")
        assert len(fr.actions) == 0  # not in unconditional actions
        assert len(fr.conditions) == 1
        assert len(fr.conditions[0].actions) == 1
        assert fr.conditions[0].actions[0]["record_type_name"] == "new_type"

    def test_validate_condition_without_actions_raises(self):
        """validate() raises when a non-else condition has no actions."""
        fr = FlowRecord("test_type")
        fr.if_(FlowResult("r", ["x"]) == 1)
        # Condition exists but has no actions
        with pytest.raises(ValueError, match="has no actions"):
            fr.validate()

    def test_validate_passes_for_complete_flow(self):
        """validate() returns True for a properly defined flow."""
        fr = FlowRecord("test_type")
        fr.on_status("finished")
        fr.if_(FlowResult("r", ["x"]) == 1).add_record("output")
        assert fr.validate() is True

    def test_call_action(self):
        """call() adds a call_function action."""

        def my_handler(**kwargs):
            pass

        fr = FlowRecord("test_type")
        fr.call(my_handler)
        assert len(fr.actions) == 1
        assert fr.actions[0]["type"] == "call_function"
        assert fr.actions[0]["function"] is my_handler

    def test_update_record_action(self):
        """update_record() adds an update_record action."""
        fr = FlowRecord("test_type")
        fr.update_record("target", status="finished")
        assert len(fr.actions) == 1
        assert fr.actions[0]["type"] == "update_record"
        assert fr.actions[0]["record_name"] == "target"
        assert fr.actions[0]["params"]["status"] == "finished"


# ─── FlowCondition — condition evaluation ─────────────────────────────────────


class TestFlowCondition:
    """Tests for FlowCondition evaluation logic."""

    def test_evaluate_true(self):
        """Condition evaluates to True when comparison matches."""
        ctx = {"r": make_record_read("r", data={"x": 10})}
        cmp = FlowResult("r", ["x"]) == 10
        condition = FlowCondition(cmp)
        assert condition.evaluate(ctx) is True

    def test_evaluate_false(self):
        """Condition evaluates to False when comparison doesn't match."""
        ctx = {"r": make_record_read("r", data={"x": 10})}
        cmp = FlowResult("r", ["x"]) == 99
        condition = FlowCondition(cmp)
        assert condition.evaluate(ctx) is False

    def test_evaluate_none_always_true(self):
        """Condition with None comparison always evaluates to True."""
        condition = FlowCondition(None)
        assert condition.evaluate({}) is True

    def test_else_condition_always_true(self):
        """Else condition always evaluates to True."""
        condition = FlowCondition(None, is_else=True)
        assert condition.evaluate({}) is True

    def test_add_action(self):
        """add_action() appends to the actions list."""
        condition = FlowCondition(None)
        action = {"type": "create_record", "record_type_name": "test"}
        condition.add_action(action)
        assert len(condition.actions) == 1
        assert condition.actions[0] is action
