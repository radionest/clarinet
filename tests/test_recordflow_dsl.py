"""Unit tests for RecordFlow DSL — expression tree, comparisons, and builder.

Pure logic tests, no I/O or database. Uses real Pydantic models as context.
"""

from datetime import UTC, datetime

import pytest

from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.patient import PatientBase
from clarinet.models.record import RecordRead, RecordTypeBase
from clarinet.models.study import StudyBase
from clarinet.services.recordflow import (
    ENTITY_REGISTRY,
    FILE_REGISTRY,
    RECORD_REGISTRY,
    CallFunctionAction,
    ConstantFlowResult,
    CreateRecordAction,
    Field,
    FieldComparison,
    FlowCondition,
    FlowFileRecord,
    FlowRecord,
    FlowResult,
    InvalidateRecordsAction,
    LogicalComparison,
    PipelineAction,
    UpdateRecordAction,
)
from clarinet.services.recordflow.flow_file import file
from clarinet.services.recordflow.flow_record import patient, record, series, study
from clarinet.services.recordflow.flow_result import AmbiguousContextError


def _mock_iter_records(records_list: list[RecordRead]):
    """Create a side_effect that makes mock.iter_records() return an async generator."""

    async def _iter(*_args, **_kwargs):
        for r in records_list:
            yield r

    return _iter


def make_record_read(
    name: str,
    data: dict | None = None,
    record_id: int = 1,
    status: RecordStatus = RecordStatus.pending,
) -> RecordRead:
    """Create a RecordRead instance without DB for use as expression context."""
    # Pad short names to satisfy RecordTypeBase min_length=5
    type_name = name if len(name) >= 5 else f"{name}-type"
    return RecordRead(
        id=record_id,
        data=data,
        status=status,
        record_type_name=type_name,
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
        record_type=RecordTypeBase(name=type_name, level=DicomQueryLevel.STUDY),
    )


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global registries before each test."""
    from clarinet.services.pipeline.chain import _PIPELINE_REGISTRY, _TASK_REGISTRY

    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    _TASK_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    _TASK_REGISTRY.clear()


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
        result = FlowResult("missing-record", ["field"])
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


# ─── Strategy: any / all / single semantics ──────────────────────────────────


class TestStrategyResolution:
    """Strategy modifiers (.any()/.all()) and ambiguity detection."""

    def test_any_returns_flow_result_with_any_strategy(self):
        fr = FlowResult("first-check", strategy="any")
        assert fr.strategy == "any"
        # `.d.field` propagates strategy through __getattr__
        chained = fr.d.is_good
        assert chained.strategy == "any"
        assert chained.field_path == ["is_good"]

    def test_record_any_creates_any_strategy(self):
        fr = record("first-check").any()
        assert isinstance(fr, FlowResult)
        assert fr.strategy == "any"

    def test_record_all_creates_all_strategy(self):
        fr = record("first-check").all()
        assert isinstance(fr, FlowResult)
        assert fr.strategy == "all"

    def test_single_strategy_raises_on_multiple_records(self):
        ctx = {
            "first-check": [
                make_record_read("first-check", data={"is_good": True}, record_id=1),
                make_record_read("first-check", data={"is_good": False}, record_id=2),
            ]
        }
        cmp = FlowResult("first-check", ["is_good"]) == True  # noqa: E712
        with pytest.raises(AmbiguousContextError, match="Found 2 records"):
            cmp.evaluate(ctx)

    def test_any_strategy_true_when_any_match(self):
        ctx = {
            "first-check": [
                make_record_read("first-check", data={"is_good": False}, record_id=1),
                make_record_read("first-check", data={"is_good": True}, record_id=2),
            ]
        }
        cmp = record("first-check").any().d.is_good == True  # noqa: E712
        assert cmp.evaluate(ctx) is True

    def test_any_strategy_false_when_none_match(self):
        ctx = {
            "first-check": [
                make_record_read("first-check", data={"is_good": False}, record_id=1),
                make_record_read("first-check", data={"is_good": False}, record_id=2),
            ]
        }
        cmp = record("first-check").any().d.is_good == True  # noqa: E712
        assert cmp.evaluate(ctx) is False

    def test_all_strategy_true_when_all_match(self):
        ctx = {
            "first-check": [
                make_record_read("first-check", data={"is_good": True}, record_id=1),
                make_record_read("first-check", data={"is_good": True}, record_id=2),
            ]
        }
        cmp = record("first-check").all().d.is_good == True  # noqa: E712
        assert cmp.evaluate(ctx) is True

    def test_all_strategy_false_when_one_mismatches(self):
        ctx = {
            "first-check": [
                make_record_read("first-check", data={"is_good": True}, record_id=1),
                make_record_read("first-check", data={"is_good": False}, record_id=2),
            ]
        }
        cmp = record("first-check").all().d.is_good == True  # noqa: E712
        assert cmp.evaluate(ctx) is False

    def test_all_strategy_false_on_empty_list(self):
        ctx: dict[str, list[RecordRead]] = {"first-check": []}
        cmp = record("first-check").all().d.is_good == True  # noqa: E712
        assert cmp.evaluate(ctx) is False

    def test_any_strategy_false_on_empty_list(self):
        ctx: dict[str, list[RecordRead]] = {"first-check": []}
        cmp = record("first-check").any().d.is_good == True  # noqa: E712
        assert cmp.evaluate(ctx) is False

    def test_any_with_lt_operator(self):
        ctx = {
            "measurement": [
                make_record_read("measurement", data={"value": 50}, record_id=1),
                make_record_read("measurement", data={"value": 200}, record_id=2),
            ]
        }
        cmp = record("measurement").any().d.value > 100
        assert cmp.evaluate(ctx) is True

    def test_all_with_gt_operator(self):
        ctx = {
            "measurement": [
                make_record_read("measurement", data={"value": 150}, record_id=1),
                make_record_read("measurement", data={"value": 200}, record_id=2),
            ]
        }
        cmp = record("measurement").all().d.value > 100
        assert cmp.evaluate(ctx) is True

        ctx2 = {
            "measurement": [
                make_record_read("measurement", data={"value": 50}, record_id=1),
                make_record_read("measurement", data={"value": 200}, record_id=2),
            ]
        }
        assert cmp.evaluate(ctx2) is False

    def test_two_multivalued_sides_unsupported(self):
        """any/all on both sides is rejected — must reduce one side."""
        ctx = {
            "a": [make_record_read("a", data={"v": 1}, record_id=1)],
            "b": [make_record_read("b", data={"v": 1}, record_id=2)],
        }
        cmp = record("a").any().d.v == record("b").any().d.v
        with pytest.raises(NotImplementedError, match="multi-valued"):
            cmp.evaluate(ctx)


# ─── FlowRecord DSL — builder ────────────────────────────────────────────────


class TestFlowRecordDSL:
    """Tests for the FlowRecord builder DSL."""

    def test_record_factory_creates_and_registers(self):
        """record() creates a new FlowRecord and adds it to RECORD_REGISTRY."""
        fr = record("test-record-type")
        assert isinstance(fr, FlowRecord)
        assert fr.record_name == "test-record-type"
        assert fr in RECORD_REGISTRY

    def test_record_factory_creates_separate_instances(self):
        """record() always creates a new FlowRecord for independent flow definitions."""
        fr1 = record("same-record-type")
        fr2 = record("same-record-type")
        assert fr1 is not fr2
        assert fr1.record_name == fr2.record_name
        assert len(RECORD_REGISTRY) == 2

    def test_on_data_update_sets_trigger(self):
        """on_data_update() sets data_update_trigger flag."""
        fr = FlowRecord("test-type")
        result = fr.on_data_update()
        assert result is fr
        assert fr.data_update_trigger is True

    def test_invalidate_records_unconditional(self):
        """invalidate_records() without if_() adds to self.actions."""
        fr = FlowRecord("test-type")
        fr.invalidate_records("child-a", "child-b", mode="hard")
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], InvalidateRecordsAction)
        assert fr.actions[0].record_type_names == ["child-a", "child-b"]
        assert fr.actions[0].mode == "hard"

    def test_invalidate_records_conditional(self):
        """invalidate_records() after if_() adds to condition.actions."""
        fr = FlowRecord("test-type")
        fr.if_(FlowResult("r", ["x"]) == 1).invalidate_records("child", mode="soft")
        assert len(fr.actions) == 0
        assert len(fr.conditions) == 1
        assert isinstance(fr.conditions[0].actions[0], InvalidateRecordsAction)
        assert fr.conditions[0].actions[0].mode == "soft"

    def test_invalidate_records_with_callback(self):
        """invalidate_records() stores callback when provided."""

        def my_handler(**kwargs: object) -> None:
            pass

        fr = FlowRecord("test-type")
        fr.invalidate_records("child", callback=my_handler)
        assert isinstance(fr.actions[0], InvalidateRecordsAction)
        assert fr.actions[0].callback is my_handler

    def test_is_active_flow_with_trigger(self):
        """is_active_flow() returns True when flow has a trigger."""
        fr = FlowRecord("test-type")
        assert fr.is_active_flow() is False

        fr.on_status("finished")
        assert fr.is_active_flow() is True

    def test_is_active_flow_with_data_update(self):
        """is_active_flow() returns True for data_update_trigger."""
        fr = FlowRecord("test-type")
        fr.on_data_update()
        assert fr.is_active_flow() is True

    def test_is_active_flow_with_actions(self):
        """is_active_flow() returns True when flow has actions."""
        fr = FlowRecord("test-type")
        fr.add_record("other")
        assert fr.is_active_flow() is True

    def test_reference_only_flow_is_not_active(self):
        """FlowRecord used only for .data references is not active."""
        fr = record("ref-type")
        _ = fr.data.some_field  # Only used for data reference
        assert fr.is_active_flow() is False

    def test_on_status_sets_trigger(self):
        """on_status() sets status_trigger string."""
        fr = FlowRecord("test-type")
        result = fr.on_status("finished")
        assert result is fr  # returns self for chaining
        assert fr.status_trigger == "finished"

    def test_on_status_with_enum(self):
        """on_status() accepts RecordStatus enum."""
        fr = FlowRecord("test-type")
        fr.on_status(RecordStatus.finished)
        assert fr.status_trigger == "finished"

    def test_if_or_and_chaining(self):
        """if_().or_().and_() builds nested LogicalComparison."""
        fr = FlowRecord("test-type")
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
        fr = FlowRecord("test-type")
        with pytest.raises(ValueError, match=r"or_.*must be called after if_"):
            fr.or_(FlowResult("r", ["x"]) == 1)

    def test_and_without_if_raises(self):
        """and_() without preceding if_() raises ValueError."""
        fr = FlowRecord("test-type")
        with pytest.raises(ValueError, match=r"and_.*must be called after if_"):
            fr.and_(FlowResult("r", ["x"]) == 1)

    def test_else_without_if_raises(self):
        """else_() without preceding if_() raises ValueError."""
        fr = FlowRecord("test-type")
        with pytest.raises(ValueError, match=r"else_.*must be called after if_"):
            fr.else_()

    def test_add_record_unconditional(self):
        """add_record() without if_() adds to self.actions."""
        fr = FlowRecord("test-type")
        fr.add_record("new-type")
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], CreateRecordAction)
        assert fr.actions[0].record_type_name == "new-type"

    def test_add_record_conditional(self):
        """add_record() after if_() adds to condition.actions."""
        fr = FlowRecord("test-type")
        fr.if_(FlowResult("r", ["x"]) == 1).add_record("new-type")
        assert len(fr.actions) == 0  # not in unconditional actions
        assert len(fr.conditions) == 1
        assert len(fr.conditions[0].actions) == 1
        assert isinstance(fr.conditions[0].actions[0], CreateRecordAction)
        assert fr.conditions[0].actions[0].record_type_name == "new-type"

    def test_create_record_default_inherit_user_false(self):
        """CreateRecordAction.inherit_user defaults to False."""
        fr = FlowRecord("test-type")
        fr.add_record("child")
        assert isinstance(fr.actions[0], CreateRecordAction)
        assert fr.actions[0].inherit_user is False

    def test_create_record_explicit_inherit_user_true(self):
        """create_record() with inherit_user=True passes flag to actions."""
        fr = FlowRecord("test-type")
        fr.create_record("child-a", "child-b", inherit_user=True)
        assert len(fr.actions) == 2
        for action in fr.actions:
            assert isinstance(action, CreateRecordAction)
            assert action.inherit_user is True

    def test_add_record_inherit_user_kwarg(self):
        """add_record() passes inherit_user kwarg to CreateRecordAction."""
        fr = FlowRecord("test-type")
        fr.add_record("child", inherit_user=True)
        assert isinstance(fr.actions[0], CreateRecordAction)
        assert fr.actions[0].inherit_user is True

    def test_validate_condition_without_actions_raises(self):
        """validate() raises when a non-else condition has no actions."""
        fr = FlowRecord("test-type")
        fr.if_(FlowResult("r", ["x"]) == 1)
        # Condition exists but has no actions
        with pytest.raises(ValueError, match="has no actions"):
            fr.validate()

    def test_validate_passes_for_complete_flow(self):
        """validate() returns True for a properly defined flow."""
        fr = FlowRecord("test-type")
        fr.on_status("finished")
        fr.if_(FlowResult("r", ["x"]) == 1).add_record("output")
        assert fr.validate() is True

    def test_call_action(self):
        """call() adds a call_function action."""

        def my_handler(**kwargs):
            pass

        fr = FlowRecord("test-type")
        fr.call(my_handler)
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], CallFunctionAction)
        assert fr.actions[0].function is my_handler

    def test_update_record_action(self):
        """update_record() adds an update_record action."""
        fr = FlowRecord("test-type")
        fr.update_record("target", status="finished")
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], UpdateRecordAction)
        assert fr.actions[0].record_name == "target"
        assert fr.actions[0].status == "finished"
        assert fr.actions[0].strategy == "single"

    def test_update_record_strategy_all(self):
        """update_record(strategy='all') stores 'all' on the action."""
        fr = FlowRecord("test-type")
        fr.update_record("target", status="finished", strategy="all")
        assert isinstance(fr.actions[0], UpdateRecordAction)
        assert fr.actions[0].strategy == "all"

    def test_do_task_creates_pipeline_action(self):
        """do_task() creates a PipelineAction with _task: prefix."""
        from unittest.mock import MagicMock

        from clarinet.services.pipeline.chain import _PIPELINE_REGISTRY

        mock_task = MagicMock()
        mock_task.task_name = "do_task_test_fn"

        fr = FlowRecord("test-type")
        result = fr.do_task(mock_task)

        assert result is fr  # returns self for chaining
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], PipelineAction)
        assert fr.actions[0].pipeline_name == "_task:do_task_test_fn"

        # Auto-Pipeline registered with one step
        pipeline = _PIPELINE_REGISTRY["_task:do_task_test_fn"]
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].task is mock_task

    def test_do_task_dedup_reuses_pipeline(self):
        """Calling do_task() twice with the same task reuses one Pipeline."""
        from unittest.mock import MagicMock

        from clarinet.services.pipeline.chain import _PIPELINE_REGISTRY

        mock_task = MagicMock()
        mock_task.task_name = "dedup_test_fn"

        fr1 = FlowRecord("type-a")
        fr1.do_task(mock_task)

        fr2 = FlowRecord("type-b")
        fr2.do_task(mock_task)

        # Only one Pipeline created
        assert "_task:dedup_test_fn" in _PIPELINE_REGISTRY
        pipeline = _PIPELINE_REGISTRY["_task:dedup_test_fn"]
        assert len(pipeline.steps) == 1

    def test_do_task_conditional(self):
        """do_task() after if_() attaches to condition.actions."""
        from unittest.mock import MagicMock

        mock_task = MagicMock()
        mock_task.task_name = "cond_do_task_fn"

        fr = FlowRecord("test-type")
        fr.if_(FlowResult("r", ["x"]) == 1).do_task(mock_task)

        assert len(fr.actions) == 0
        assert len(fr.conditions) == 1
        assert len(fr.conditions[0].actions) == 1
        assert isinstance(fr.conditions[0].actions[0], PipelineAction)
        assert fr.conditions[0].actions[0].pipeline_name == "_task:cond_do_task_fn"

    def test_do_task_extra_payload(self):
        """do_task() passes extra_payload through to PipelineAction."""
        from unittest.mock import MagicMock

        mock_task = MagicMock()
        mock_task.task_name = "payload_test_fn"

        fr = FlowRecord("test-type")
        fr.do_task(mock_task, threshold=0.5, mode="fast")

        action = fr.actions[0]
        assert isinstance(action, PipelineAction)
        assert action.extra_payload == {"threshold": 0.5, "mode": "fast"}


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
        action = CreateRecordAction(record_type_name="test-type")
        condition.add_action(action)
        assert len(condition.actions) == 1
        assert condition.actions[0] is action


# ─── RecordFlowEngine — unit tests with mocked client ────────────────────────


class TestRecordFlowEngineUnit:
    """Unit tests for RecordFlowEngine with mocked ClarinetClient."""

    @pytest.mark.asyncio
    async def test_handle_data_update_only_runs_data_update_flows(self):
        """handle_record_data_update only executes flows with on_data_update."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        # Create mocked client
        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])

        engine = RecordFlowEngine(mock_client)

        # Register two flows: one on_status, one on_data_update
        flow_status = FlowRecord("test-type")
        flow_status.on_status("finished")
        flow_status.add_record("output-from-status")

        flow_data_update = FlowRecord("test-type")
        flow_data_update.on_data_update()
        flow_data_update.add_record("output-from-data-update")

        engine.register_flow(flow_status)
        engine.register_flow(flow_data_update)

        # Create test record
        test_record = make_record_read("test-type", record_id=100, status=RecordStatus.finished)

        # Call handle_record_data_update
        await engine.handle_record_data_update(test_record)

        # Verify only data_update flow executed (create_record should be called once)
        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "output-from-data-update"

    @pytest.mark.asyncio
    async def test_handle_status_change_skips_data_update_flows(self):
        """handle_record_status_change skips flows with on_data_update."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        # Create mocked client
        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])

        engine = RecordFlowEngine(mock_client)

        # Register two flows: one on_status, one on_data_update
        flow_status = FlowRecord("test-type")
        flow_status.on_status("finished")
        flow_status.add_record("output-from-status")

        flow_data_update = FlowRecord("test-type")
        flow_data_update.on_data_update()
        flow_data_update.add_record("output-from-data-update")

        engine.register_flow(flow_status)
        engine.register_flow(flow_data_update)

        # Create test record
        test_record = make_record_read("test-type", record_id=100, status=RecordStatus.finished)

        # Call handle_record_status_change
        await engine.handle_record_status_change(test_record)

        # Verify only status flow executed
        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "output-from-status"

    @pytest.mark.asyncio
    async def test_invalidate_records_hard_mode(self):
        """_invalidate_records calls invalidate_record with mode='hard'."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        # Create mocked client
        mock_client = AsyncMock()

        # Source record
        source_record = make_record_read("source-type", record_id=1, status=RecordStatus.finished)

        # Target record to invalidate
        target_record = make_record_read("child-type", record_id=2, status=RecordStatus.finished)

        # Mock find_records to return the target
        mock_client.find_records = AsyncMock(return_value=[target_record])
        mock_client.iter_records = _mock_iter_records([target_record])
        mock_client.invalidate_record = AsyncMock(return_value=target_record)

        engine = RecordFlowEngine(mock_client)

        # Register flow with invalidate_records
        flow = FlowRecord("source-type")
        flow.on_status("finished")
        flow.invalidate_records("child-type", mode="hard")

        engine.register_flow(flow)

        # Execute flow
        await engine.handle_record_status_change(source_record)

        # Verify invalidate_record was called correctly
        assert mock_client.invalidate_record.call_count == 1
        call_kwargs = mock_client.invalidate_record.call_args[1]
        assert call_kwargs["record_id"] == 2
        assert call_kwargs["mode"] == "hard"
        assert call_kwargs["source_record_id"] == 1

    @pytest.mark.asyncio
    async def test_invalidate_records_skips_self(self):
        """_invalidate_records skips source record when it appears in results."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        # Create mocked client
        mock_client = AsyncMock()

        # Source record
        source_record = make_record_read("test-type", record_id=1, status=RecordStatus.finished)

        # Mock find_records to return source record itself
        mock_client.find_records = AsyncMock(return_value=[source_record])
        mock_client.invalidate_record = AsyncMock()

        engine = RecordFlowEngine(mock_client)

        # Register flow that tries to invalidate same type
        flow = FlowRecord("test-type")
        flow.on_status("finished")
        flow.invalidate_records("test-type", mode="hard")

        engine.register_flow(flow)

        # Execute flow
        await engine.handle_record_status_change(source_record)

        # Verify invalidate_record was NOT called
        assert mock_client.invalidate_record.call_count == 0

    @pytest.mark.asyncio
    async def test_invalidate_records_with_callback(self):
        """_invalidate_records calls callback with correct kwargs."""
        from unittest.mock import AsyncMock, Mock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        # Create mocked client
        mock_client = AsyncMock()

        # Source and target records
        source_record = make_record_read("source-type", record_id=1, status=RecordStatus.finished)
        target_record = make_record_read("child-type", record_id=2, status=RecordStatus.finished)

        # Mock find_records to return target
        mock_client.find_records = AsyncMock(return_value=[target_record])
        mock_client.iter_records = _mock_iter_records([target_record])
        mock_client.invalidate_record = AsyncMock(return_value=target_record)

        # Mock callback
        callback_mock = Mock()

        engine = RecordFlowEngine(mock_client)

        # Register flow with callback
        flow = FlowRecord("source-type")
        flow.on_status("finished")
        flow.invalidate_records("child-type", mode="hard", callback=callback_mock)

        engine.register_flow(flow)

        # Execute flow
        await engine.handle_record_status_change(source_record)

        # Verify callback was called with correct kwargs
        assert callback_mock.call_count == 1
        call_kwargs = callback_mock.call_args[1]
        assert call_kwargs["record"] == target_record
        assert call_kwargs["source_record"] == source_record
        assert call_kwargs["client"] == mock_client

    @pytest.mark.asyncio
    async def test_invalidate_records_soft_mode(self):
        """_invalidate_records calls invalidate_record with mode='soft'."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        # Create mocked client
        mock_client = AsyncMock()

        # Source and target records
        source_record = make_record_read("source-type", record_id=1, status=RecordStatus.finished)
        target_record = make_record_read("child-type", record_id=2, status=RecordStatus.finished)

        # Mock find_records to return target
        mock_client.find_records = AsyncMock(return_value=[target_record])
        mock_client.iter_records = _mock_iter_records([target_record])
        mock_client.invalidate_record = AsyncMock(return_value=target_record)

        engine = RecordFlowEngine(mock_client)

        # Register flow with soft mode
        flow = FlowRecord("source-type")
        flow.on_status("finished")
        flow.invalidate_records("child-type", mode="soft")

        engine.register_flow(flow)

        # Execute flow
        await engine.handle_record_status_change(source_record)

        # Verify invalidate_record was called with mode='soft'
        assert mock_client.invalidate_record.call_count == 1
        call_kwargs = mock_client.invalidate_record.call_args[1]
        assert call_kwargs["mode"] == "soft"

    @pytest.mark.asyncio
    async def test_invalidate_records_with_multiple_targets(self):
        """_invalidate_records processes multiple target record types."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        # Create mocked client
        mock_client = AsyncMock()

        # Source record
        source_record = make_record_read("source-type", record_id=1, status=RecordStatus.finished)

        # Multiple target records
        target_a = make_record_read("child-a", record_id=2, status=RecordStatus.finished)
        target_b = make_record_read("child-b", record_id=3, status=RecordStatus.finished)

        # Mock iter_records to return different targets based on record_type_name
        async def mock_iter_records(**kwargs):
            if kwargs.get("record_type_name") == "child-a":
                yield target_a
            elif kwargs.get("record_type_name") == "child-b":
                yield target_b

        mock_client.iter_records = mock_iter_records
        mock_client.invalidate_record = AsyncMock()

        engine = RecordFlowEngine(mock_client)

        # Register flow with multiple invalidation targets
        flow = FlowRecord("source-type")
        flow.on_status("finished")
        flow.invalidate_records("child-a", "child-b", mode="hard")

        engine.register_flow(flow)

        # Execute flow
        await engine.handle_record_status_change(source_record)

        # Verify invalidate_record was called for both targets
        assert mock_client.invalidate_record.call_count == 2

        # Check both calls
        calls = mock_client.invalidate_record.call_args_list
        invalidated_ids = {call[1]["record_id"] for call in calls}
        assert invalidated_ids == {2, 3}


# ─── Engine — inherit_user + explicit parent_record_id ───────────────────────


class TestEngineInheritUserAndParent:
    """Unit tests for inherit_user flag and explicit parent_record_id."""

    @pytest.mark.asyncio
    async def test_engine_no_user_inherit_by_default(self):
        """Engine does not inherit user_id without inherit_user=True."""
        from unittest.mock import AsyncMock
        from uuid import UUID

        from clarinet.services.recordflow.engine import RecordFlowEngine

        admin_uuid = UUID("00000000-0000-0000-0000-000000000001")

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(return_value=make_record_read("output", record_id=99))

        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.add_record("output")  # inherit_user defaults to False
        engine.register_flow(flow)

        test_record = make_record_read("trigger-type", record_id=10, status=RecordStatus.finished)
        test_record.user_id = admin_uuid

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.user_id is None

    @pytest.mark.asyncio
    async def test_engine_inherit_user_true(self):
        """Engine inherits user_id when inherit_user=True."""
        from unittest.mock import AsyncMock
        from uuid import UUID

        from clarinet.services.recordflow.engine import RecordFlowEngine

        admin_uuid = UUID("00000000-0000-0000-0000-000000000001")

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(return_value=make_record_read("output", record_id=99))

        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.add_record("output", inherit_user=True)
        engine.register_flow(flow)

        test_record = make_record_read("trigger-type", record_id=10, status=RecordStatus.finished)
        test_record.user_id = admin_uuid

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert str(call_args.user_id) == str(admin_uuid)

    @pytest.mark.asyncio
    async def test_engine_explicit_user_id_overrides_all(self):
        """Explicit user_id in add_record() takes priority over inherit_user."""
        from unittest.mock import AsyncMock
        from uuid import UUID

        from clarinet.services.recordflow.engine import RecordFlowEngine

        admin_uuid = UUID("00000000-0000-0000-0000-000000000001")
        explicit_uuid = "00000000-0000-0000-0000-000000000002"

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(return_value=make_record_read("output", record_id=99))

        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.add_record("output", user_id=explicit_uuid, inherit_user=True)
        engine.register_flow(flow)

        test_record = make_record_read("trigger-type", record_id=10, status=RecordStatus.finished)
        test_record.user_id = admin_uuid

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert str(call_args.user_id) == explicit_uuid

    @pytest.mark.asyncio
    async def test_engine_explicit_parent_record_id_preserved(self):
        """Explicit parent_record_id in add_record() is passed through to create_record."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(
            return_value=make_record_read("child-type", record_id=99)
        )

        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("parent-type")
        flow.on_status("finished")
        flow.add_record("child-type", parent_record_id=42)
        engine.register_flow(flow)

        test_record = make_record_read("parent-type", record_id=10, status=RecordStatus.finished)

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.parent_record_id == 42


# ─── Entity creation flows — DSL + engine ──────────────────────────────────


class TestEntityFlowDSL:
    """Tests for entity creation flow DSL (series, study, patient factories)."""

    def test_series_factory_creates_flow_with_entity_trigger(self):
        """series() creates a FlowRecord with entity_trigger='series'."""
        fr = series()
        assert isinstance(fr, FlowRecord)
        assert fr.entity_trigger == "series"
        assert fr.record_name == "series"

    def test_study_factory_creates_flow_with_entity_trigger(self):
        """study() creates a FlowRecord with entity_trigger='study'."""
        fr = study()
        assert fr.entity_trigger == "study"
        assert fr.record_name == "study"

    def test_patient_factory_creates_flow_with_entity_trigger(self):
        """patient() creates a FlowRecord with entity_trigger='patient'."""
        fr = patient()
        assert fr.entity_trigger == "patient"
        assert fr.record_name == "patient"

    def test_entity_factory_populates_entity_registry(self):
        """Entity factories add flows to ENTITY_REGISTRY, not RECORD_REGISTRY."""
        series()
        study()
        patient()
        assert len(ENTITY_REGISTRY) == 3
        assert len(RECORD_REGISTRY) == 0

    def test_on_created_returns_self(self):
        """on_created() returns self for method chaining."""
        fr = series()
        result = fr.on_created()
        assert result is fr

    def test_entity_flow_chaining(self):
        """series().on_created().add_record('X') creates correct flow."""
        fr = series().on_created().add_record("series-markup")
        assert fr.entity_trigger == "series"
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], CreateRecordAction)
        assert fr.actions[0].record_type_name == "series-markup"

    def test_entity_flow_is_active(self):
        """Entity flow with entity_trigger is considered active."""
        fr = series()
        assert fr.is_active_flow() is True

    def test_entity_flow_repr(self):
        """Entity flow __repr__ shows entity type."""
        fr = series().on_created().add_record("series-markup")
        assert repr(fr) == "series().on_created()"

    def test_entity_flow_call_action(self):
        """Entity flows support .call() for custom functions."""

        def my_handler(**kwargs: object) -> None:
            pass

        fr = series().on_created().call(my_handler)
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], CallFunctionAction)
        assert fr.actions[0].function is my_handler


class TestEntityFlowEngine:
    """Unit tests for entity flow registration and execution in RecordFlowEngine."""

    def test_register_entity_flow_routes_to_entity_flows(self):
        """register_flow() routes entity flows to engine.entity_flows."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        engine = RecordFlowEngine(mock_client)

        fr = FlowRecord("series", entity_trigger="series")
        fr.add_record("series-markup")
        engine.register_flow(fr)

        assert "series" in engine.entity_flows
        assert len(engine.entity_flows["series"]) == 1
        assert len(engine.flows) == 0  # not in record flows

    @pytest.mark.asyncio
    async def test_handle_entity_created_creates_record(self):
        """handle_entity_created() executes create_record action."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.create_record = AsyncMock(
            return_value=make_record_read("series-markup", record_id=99)
        )

        engine = RecordFlowEngine(mock_client)

        fr = FlowRecord("series", entity_trigger="series")
        fr.add_record("series-markup")
        engine.register_flow(fr)

        await engine.handle_entity_created(
            "series", patient_id="PAT001", study_uid="1.2.3", series_uid="1.2.3.4"
        )

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "series-markup"
        assert call_args.patient_id == "PAT001"
        assert call_args.study_uid == "1.2.3"
        assert call_args.series_uid == "1.2.3.4"

    @pytest.mark.asyncio
    async def test_handle_entity_created_no_matching_flows(self):
        """handle_entity_created() does nothing when no flows match."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        engine = RecordFlowEngine(mock_client)

        # Register a series flow, but trigger for study
        fr = FlowRecord("series", entity_trigger="series")
        fr.add_record("series-markup")
        engine.register_flow(fr)

        await engine.handle_entity_created("study", patient_id="PAT001", study_uid="1.2.3")

        assert mock_client.create_record.call_count == 0

    @pytest.mark.asyncio
    async def test_handle_entity_created_calls_function(self):
        """handle_entity_created() executes call_function action with entity kwargs."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        engine = RecordFlowEngine(mock_client)

        call_log: list[dict] = []

        def entity_handler(**kwargs: object) -> None:
            call_log.append(dict(kwargs))

        fr = FlowRecord("series", entity_trigger="series")
        fr.call(entity_handler)
        engine.register_flow(fr)

        await engine.handle_entity_created(
            "series", patient_id="PAT001", study_uid="1.2.3", series_uid="1.2.3.4"
        )

        assert len(call_log) == 1
        assert call_log[0]["patient_id"] == "PAT001"
        assert call_log[0]["study_uid"] == "1.2.3"
        assert call_log[0]["series_uid"] == "1.2.3.4"
        assert call_log[0]["client"] is mock_client


# ─── Field proxy — self-referential conditions ─────────────────────────────


class TestFieldProxy:
    """Tests for Field (F) proxy and if_record() method."""

    def test_field_getattr_builds_self_ref(self):
        """F.name creates FlowResult with __self__ record_name."""
        F = Field()
        ref = F.study_type
        assert isinstance(ref, FlowResult)
        assert ref.record_name == "__self__"
        assert ref.field_path == ["study_type"]

    def test_field_nested_access(self):
        """F.findings.tumor_size builds nested field_path."""
        F = Field()
        ref = F.findings.tumor_size
        assert ref.record_name == "__self__"
        assert ref.field_path == ["findings", "tumor_size"]

    def test_field_comparison_with_constant(self):
        """F.field == value creates FieldComparison with ConstantFlowResult."""
        F = Field()
        cmp = F.is_good == True  # noqa: E712
        assert isinstance(cmp, FieldComparison)
        assert cmp.left.record_name == "__self__"
        assert cmp.left.field_path == ["is_good"]
        assert isinstance(cmp.right, ConstantFlowResult)

    def test_field_evaluate_resolves_from_self(self):
        """F-based comparison resolves __self__ key from context."""
        F = Field()
        cmp = F.score > 50
        ctx = {"__self__": make_record_read("any-type", data={"score": 75})}
        assert cmp.evaluate(ctx) is True

    def test_field_evaluate_false(self):
        """F-based comparison returns False when not met."""
        F = Field()
        cmp = F.score > 50
        ctx = {"__self__": make_record_read("any-type", data={"score": 30})}
        assert cmp.evaluate(ctx) is False

    def test_field_nested_evaluate(self):
        """F.a.b navigates nested data correctly."""
        F = Field()
        cmp = F.findings.tumor_size == 3.5
        ctx = {"__self__": make_record_read("r", data={"findings": {"tumor_size": 3.5}})}
        assert cmp.evaluate(ctx) is True

    def test_field_all_operators(self):
        """Field proxy works with all comparison operators."""
        F = Field()
        ctx = {"__self__": make_record_read("r", data={"v": 10})}

        assert (F.v == 10).evaluate(ctx) is True
        assert (F.v != 5).evaluate(ctx) is True
        assert (F.v < 20).evaluate(ctx) is True
        assert (F.v <= 10).evaluate(ctx) is True
        assert (F.v > 5).evaluate(ctx) is True
        assert (F.v >= 10).evaluate(ctx) is True


class TestIfRecord:
    """Tests for FlowRecord.if_record() method."""

    def test_if_record_single_condition(self):
        """if_record() with one condition creates a single FlowCondition."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.active == True).add_record("output")  # noqa: E712
        assert len(fr.conditions) == 1
        assert len(fr.conditions[0].actions) == 1

    def test_if_record_multiple_conditions_and_semantics(self):
        """if_record(A, B) combines with AND logic."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.is_good == True, F.study_type == "CT").add_record("output")  # noqa: E712

        assert len(fr.conditions) == 1
        condition = fr.conditions[0].condition
        assert isinstance(condition, LogicalComparison)
        assert condition.operator == "and"

    def test_if_record_empty_raises(self):
        """if_record() without conditions raises ValueError."""
        fr = FlowRecord("test-type")
        with pytest.raises(ValueError, match="requires at least one"):
            fr.if_record()

    def test_if_record_evaluates_true(self):
        """if_record conditions pass when data matches."""
        F = Field()
        fr = FlowRecord("first-check")
        fr.if_record(F.is_good == True, F.study_type == "CT").add_record("seg")  # noqa: E712

        ctx = {
            "__self__": make_record_read("first-check", data={"is_good": True, "study_type": "CT"}),
        }
        assert fr.conditions[0].evaluate(ctx) is True

    def test_if_record_evaluates_false_on_mismatch(self):
        """if_record AND fails when one field doesn't match."""
        F = Field()
        fr = FlowRecord("first-check")
        fr.if_record(F.is_good == True, F.study_type == "CT").add_record("seg")  # noqa: E712

        ctx = {
            "__self__": make_record_read(
                "first-check", data={"is_good": True, "study_type": "MRI"}
            ),
        }
        assert fr.conditions[0].evaluate(ctx) is False

    def test_if_record_on_missing_skip_returns_false(self):
        """Missing field with on_missing='skip' evaluates to False."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.nonexistent_field > 0).add_record("output")

        ctx = {"__self__": make_record_read("test", data={"other": 42})}
        # nonexistent_field → None, None > 0 → TypeError → skip → False
        assert fr.conditions[0].evaluate(ctx) is False

    def test_if_record_on_missing_skip_nested(self):
        """Missing nested field with on_missing='skip' evaluates to False."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.deep.nested.field == "x").add_record("output")

        ctx = {"__self__": make_record_read("test", data={"unrelated": 1})}
        # deep → None, then accessing nested on None → ValueError → skip → False
        assert fr.conditions[0].evaluate(ctx) is False

    def test_if_record_on_missing_raise_propagates(self):
        """Missing field with on_missing='raise' raises TypeError."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.missing > 0, on_missing="raise").add_record("output")

        ctx = {"__self__": make_record_read("test", data={})}
        with pytest.raises(TypeError):
            fr.conditions[0].evaluate(ctx)

    def test_if_record_on_missing_skip_is_default(self):
        """on_missing defaults to 'skip' for if_record."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.x > 0).add_record("output")
        assert fr.conditions[0].on_missing == "skip"

    def test_if_on_missing_raise_is_default(self):
        """on_missing defaults to 'raise' for regular if_."""
        fr = FlowRecord("test-type")
        fr.if_(FlowResult("r", ["x"]) == 1).add_record("output")
        assert fr.conditions[0].on_missing == "raise"

    def test_if_record_none_data_skip(self):
        """Record with None data and on_missing='skip' evaluates to False."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.anything == True).add_record("output")  # noqa: E712

        ctx = {"__self__": make_record_read("test", data=None)}
        assert fr.conditions[0].evaluate(ctx) is False


class TestFieldProxyEngineIntegration:
    """Tests for Field proxy through RecordFlowEngine."""

    @pytest.mark.asyncio
    async def test_engine_injects_self_and_evaluates_field(self):
        """Engine puts triggering record into __self__ context key."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(return_value=make_record_read("seg", record_id=99))

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("first-check")
        flow_def.on_status("finished")
        flow_def.if_record(F.is_good == True, F.study_type == "CT").add_record("seg-type")  # noqa: E712

        engine.register_flow(flow_def)

        test_record = make_record_read(
            "first-check",
            record_id=100,
            status=RecordStatus.finished,
            data={"is_good": True, "study_type": "CT"},
        )

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "seg-type"

    @pytest.mark.asyncio
    async def test_engine_field_condition_not_met_skips_action(self):
        """Engine skips actions when Field condition is not met."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("first-check")
        flow_def.on_status("finished")
        flow_def.if_record(F.is_good == True, F.study_type == "CT").add_record("seg-type")  # noqa: E712

        engine.register_flow(flow_def)

        test_record = make_record_read(
            "first-check",
            record_id=100,
            status=RecordStatus.finished,
            data={"is_good": True, "study_type": "MRI"},
        )

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 0

    @pytest.mark.asyncio
    async def test_engine_field_missing_data_skips_gracefully(self):
        """Engine gracefully skips when Field references missing data."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("compare")
        flow_def.on_status("finished")
        flow_def.if_record(F.false_positive_num > 0).add_record("update-master")

        engine.register_flow(flow_def)

        # Record has no false_positive_num field
        test_record = make_record_read(
            "compare",
            record_id=100,
            status=RecordStatus.finished,
            data={"other_field": 42},
        )

        await engine.handle_record_status_change(test_record)

        # Should not crash, should not create record
        assert mock_client.create_record.call_count == 0


# ─── FlowRecord convenience methods ─────────────────────────────────────────


class TestFlowRecordConvenience:
    """Tests for FlowRecord convenience methods (on_finished, on_creation, etc.)."""

    def test_on_finished_shortcut(self):
        """on_finished() sets status_trigger to 'finished'."""
        fr = FlowRecord("test-type")
        result = fr.on_finished()
        assert result is fr
        assert fr.status_trigger == "finished"

    def test_on_creation_alias(self):
        """on_creation() is an alias for on_created()."""
        fr = series()
        result = fr.on_creation()
        assert result is fr

    def test_create_record_single(self):
        """create_record() with one name creates one CreateRecordAction."""
        fr = FlowRecord("test-type")
        result = fr.create_record("child-type")
        assert result is fr
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], CreateRecordAction)
        assert fr.actions[0].record_type_name == "child-type"

    def test_create_record_multiple(self):
        """create_record() with multiple names creates multiple CreateRecordActions."""
        fr = FlowRecord("test-type")
        fr.create_record("child-a", "child-b", "child-c")
        assert len(fr.actions) == 3
        names = [a.record_type_name for a in fr.actions]
        assert names == ["child-a", "child-b", "child-c"]

    def test_create_record_conditional(self):
        """create_record() works after if_record()."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.if_record(F.active == True).create_record("output-a", "output-b")  # noqa: E712
        assert len(fr.actions) == 0  # unconditional list is empty
        assert len(fr.conditions) == 1
        assert len(fr.conditions[0].actions) == 2
        assert all(isinstance(a, CreateRecordAction) for a in fr.conditions[0].actions)

    def test_invalidate_all_records_alias(self):
        """invalidate_all_records() delegates to invalidate_records()."""
        fr = FlowRecord("test-type")
        fr.invalidate_all_records("child-a", "child-b", mode="soft")
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], InvalidateRecordsAction)
        assert fr.actions[0].record_type_names == ["child-a", "child-b"]
        assert fr.actions[0].mode == "soft"


# ─── Public facade — clarinet.flow ───────────────────────────────────────────────


class TestFlowFacade:
    """Tests for the public clarinet.flow facade module."""

    def test_import_primitives(self):
        """All DSL primitives are importable from clarinet.flow."""
        from clarinet.flow import Field as F
        from clarinet.flow import patient as p
        from clarinet.flow import record as r
        from clarinet.flow import series as se
        from clarinet.flow import study as st
        from clarinet.flow import task as t

        assert callable(r)
        assert callable(st)
        assert callable(se)
        assert callable(p)
        assert callable(t)
        assert callable(F)

    def test_task_bare_decorator(self):
        """@task (no parens) decorates function via pipeline_task."""
        from clarinet.flow import task

        @task
        async def my_task(msg, ctx):
            pass

        # The decorated function should have a task_name attribute (from TaskIQ)
        assert hasattr(my_task, "task_name")

    def test_task_with_kwargs(self):
        """@task(queue='q') returns a decorator."""
        from clarinet.flow import task

        decorator = task(queue="clarinet.gpu")
        assert callable(decorator)

        @decorator
        async def gpu_task(msg, ctx):
            pass

        assert hasattr(gpu_task, "task_name")


# ─── File flow DSL — builder ────────────────────────────────────────────────


class TestFileFlowDSL:
    """Tests for the file() factory and FlowFileRecord builder DSL."""

    def test_file_factory_creates_and_registers(self):
        """file() creates a FlowFileRecord and adds it to FILE_REGISTRY."""
        fr = file("master_model")
        assert isinstance(fr, FlowFileRecord)
        assert fr.file_name == "master_model"
        assert fr in FILE_REGISTRY

    def test_file_factory_with_object(self):
        """file() accepts objects with .name attribute."""
        from dataclasses import dataclass

        @dataclass
        class File:
            name: str

        fr = file(File(name="seg_output"))
        assert fr.file_name == "seg_output"
        assert fr in FILE_REGISTRY

    def test_file_factory_empty_name_raises(self):
        """file() raises ValueError for empty name."""
        with pytest.raises(ValueError, match="non-empty"):
            file("")

    def test_file_factory_no_name_attr_raises(self):
        """file() raises ValueError for objects without .name."""
        with pytest.raises(ValueError, match=r"\.name attribute"):
            file(42)

    def test_on_update_sets_trigger(self):
        """on_update() sets update_trigger flag."""
        fr = FlowFileRecord("test_file")
        result = fr.on_update()
        assert result is fr
        assert fr.update_trigger is True

    def test_invalidate_all_records_creates_action(self):
        """invalidate_all_records() creates InvalidateRecordsAction."""
        fr = FlowFileRecord("test_file")
        fr.invalidate_all_records("child-a", "child-b", mode="hard")
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], InvalidateRecordsAction)
        assert fr.actions[0].record_type_names == ["child-a", "child-b"]
        assert fr.actions[0].mode == "hard"

    def test_call_creates_action(self):
        """call() creates CallFunctionAction."""

        def my_handler(**kwargs):
            pass

        fr = FlowFileRecord("test_file")
        fr.call(my_handler)
        assert len(fr.actions) == 1
        assert isinstance(fr.actions[0], CallFunctionAction)
        assert fr.actions[0].function is my_handler

    def test_method_chaining(self):
        """Full DSL chaining works end-to-end."""
        fr = file("master_model").on_update().invalidate_all_records("projection", mode="hard")
        assert fr.file_name == "master_model"
        assert fr.update_trigger is True
        assert len(fr.actions) == 1
        assert fr.actions[0].record_type_names == ["projection"]

    def test_is_active_flow(self):
        """is_active_flow() returns True when flow has trigger or actions."""
        fr = FlowFileRecord("test_file")
        assert fr.is_active_flow() is False

        fr.on_update()
        assert fr.is_active_flow() is True

    def test_is_active_flow_with_actions_only(self):
        """is_active_flow() returns True when flow has actions without trigger."""
        fr = FlowFileRecord("test_file")
        fr.invalidate_all_records("child")
        assert fr.is_active_flow() is True

    def test_file_registry_is_separate(self):
        """FILE_REGISTRY is separate from RECORD_REGISTRY and ENTITY_REGISTRY."""
        file("test_file")
        record("test-record")
        series()
        assert len(FILE_REGISTRY) == 1
        assert len(RECORD_REGISTRY) == 1
        assert len(ENTITY_REGISTRY) == 1

    def test_repr(self):
        """FlowFileRecord __repr__ shows file name and trigger."""
        fr = file("master_model").on_update()
        assert repr(fr) == "file('master_model').on_update()"


class TestFileFlowEngine:
    """Unit tests for file flow registration and execution in RecordFlowEngine."""

    def test_register_file_flow_routes_to_file_flows(self):
        """register_flow() routes FlowFileRecord to engine.file_flows."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        engine = RecordFlowEngine(mock_client)

        fr = FlowFileRecord("master_model")
        fr.on_update().invalidate_all_records("projection")
        engine.register_flow(fr)

        assert "master_model" in engine.file_flows
        assert len(engine.file_flows["master_model"]) == 1
        assert len(engine.flows) == 0
        assert len(engine.entity_flows) == 0

    @pytest.mark.asyncio
    async def test_handle_file_update_invalidates_records(self):
        """handle_file_update() finds and invalidates matching records."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()

        target_record = make_record_read("projection", record_id=10, status=RecordStatus.finished)
        mock_client.find_records = AsyncMock(return_value=[target_record])
        mock_client.iter_records = _mock_iter_records([target_record])
        mock_client.invalidate_record = AsyncMock(return_value=target_record)

        engine = RecordFlowEngine(mock_client)

        fr = FlowFileRecord("master_model")
        fr.on_update().invalidate_all_records("projection", mode="hard")
        engine.register_flow(fr)

        await engine.handle_file_update("master_model", "PAT001")

        assert mock_client.invalidate_record.call_count == 1
        call_kwargs = mock_client.invalidate_record.call_args[1]
        assert call_kwargs["record_id"] == 10
        assert call_kwargs["mode"] == "hard"
        assert call_kwargs["source_record_id"] is None
        assert "file change" in call_kwargs["reason"]

    @pytest.mark.asyncio
    async def test_handle_file_update_unknown_file(self):
        """handle_file_update() does nothing for unknown file names."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        engine = RecordFlowEngine(mock_client)

        fr = FlowFileRecord("master_model")
        fr.on_update().invalidate_all_records("projection")
        engine.register_flow(fr)

        await engine.handle_file_update("unknown_file", "PAT001")

        assert mock_client.find_records.call_count == 0

    @pytest.mark.asyncio
    async def test_handle_file_update_calls_function(self):
        """handle_file_update() executes CallFunctionAction with file kwargs."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        engine = RecordFlowEngine(mock_client)

        call_log: list[dict] = []

        def file_handler(**kwargs):
            call_log.append(dict(kwargs))

        fr = FlowFileRecord("master_model")
        fr.on_update().call(file_handler)
        engine.register_flow(fr)

        await engine.handle_file_update("master_model", "PAT001")

        assert len(call_log) == 1
        assert call_log[0]["file_name"] == "master_model"
        assert call_log[0]["patient_id"] == "PAT001"
        assert call_log[0]["client"] is mock_client

    @pytest.mark.asyncio
    async def test_handle_file_update_skips_no_trigger(self):
        """handle_file_update() skips flows without update_trigger."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        engine = RecordFlowEngine(mock_client)

        # Flow without on_update()
        fr = FlowFileRecord("master_model")
        fr.invalidate_all_records("projection")
        engine.register_flow(fr)

        await engine.handle_file_update("master_model", "PAT001")

        assert mock_client.find_records.call_count == 0


# ─── Match/Case — pattern matching sugar ──────────────────────────────────


class TestMatchCase:
    """Tests for match()/case()/default() pattern matching DSL."""

    def test_match_case_basic(self):
        """match().case() sets match_group on conditions."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.on_finished()
        fr.match(F.study_type).case("CT").create_record("seg-ct").case("MRI").create_record(
            "seg-mri"
        )

        assert len(fr.conditions) == 2

        # Each condition has one action
        assert len(fr.conditions[0].actions) == 1
        assert fr.conditions[0].actions[0].record_type_name == "seg-ct"
        assert len(fr.conditions[1].actions) == 1
        assert fr.conditions[1].actions[0].record_type_name == "seg-mri"

        # Conditions are simple FieldComparison (no guard)
        assert isinstance(fr.conditions[0].condition, FieldComparison)
        assert isinstance(fr.conditions[1].condition, FieldComparison)

        # Both conditions share the same match_group
        assert fr.conditions[0].match_group is not None
        assert fr.conditions[0].match_group == fr.conditions[1].match_group

    def test_match_case_with_guard(self):
        """if_record().match().case() combines guard AND field == value."""
        F = Field()
        fr = FlowRecord("first-check")
        fr.on_finished()
        (
            fr.if_record(F.is_good == True)  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .case("MRI")
            .create_record("seg-mri")
        )

        assert len(fr.conditions) == 2

        # Each condition is LogicalComparison(guard AND field==value)
        for cond in fr.conditions:
            assert isinstance(cond.condition, LogicalComparison)
            assert cond.condition.operator == "and"

    def test_match_case_evaluates_correct_branch(self):
        """Only the matching case evaluates to True."""
        F = Field()
        fr = FlowRecord("first-check")
        (
            fr.if_record(F.is_good == True)  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .case("MRI")
            .create_record("seg-mri")
        )

        ctx_ct = {
            "__self__": make_record_read("first-check", data={"is_good": True, "study_type": "CT"}),
        }
        assert fr.conditions[0].evaluate(ctx_ct) is True
        assert fr.conditions[1].evaluate(ctx_ct) is False

        ctx_mri = {
            "__self__": make_record_read(
                "first-check", data={"is_good": True, "study_type": "MRI"}
            ),
        }
        assert fr.conditions[0].evaluate(ctx_mri) is False
        assert fr.conditions[1].evaluate(ctx_mri) is True

    def test_match_case_guard_false(self):
        """When guard is False, no case matches."""
        F = Field()
        fr = FlowRecord("first-check")
        (
            fr.if_record(F.is_good == True)  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .case("MRI")
            .create_record("seg-mri")
        )

        ctx = {
            "__self__": make_record_read(
                "first-check", data={"is_good": False, "study_type": "CT"}
            ),
        }
        assert fr.conditions[0].evaluate(ctx) is False
        assert fr.conditions[1].evaluate(ctx) is False

    def test_case_without_match_raises(self):
        """case() without preceding match() raises ValueError."""
        fr = FlowRecord("test-type")
        with pytest.raises(ValueError, match=r"case.*must be called after match"):
            fr.case("CT")

    def test_match_without_case_validates_error(self):
        """validate() fails when match() has no case() branches."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.match(F.study_type)
        with pytest.raises(ValueError, match="has no case"):
            fr.validate()

    def test_match_case_multiple_actions_per_case(self):
        """case().create_record('a', 'b') creates multiple actions in one case."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.match(F.study_type).case("CT").create_record("seg-single", "seg-archive")

        assert len(fr.conditions) == 1
        assert len(fr.conditions[0].actions) == 2
        names = [a.record_type_name for a in fr.conditions[0].actions]
        assert names == ["seg-single", "seg-archive"]

    def test_match_case_preserves_on_missing(self):
        """on_missing from if_record() propagates to case conditions."""
        F = Field()
        fr = FlowRecord("test-type")
        (
            fr.if_record(F.is_good == True, on_missing="raise")  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
        )

        assert fr.conditions[0].on_missing == "raise"

    def test_match_case_default_on_missing_skip(self):
        """match() without if_record() uses default on_missing='skip'."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.match(F.study_type).case("CT").create_record("seg-ct")

        assert fr.conditions[0].on_missing == "skip"

    def test_default_fires_when_no_case_matches(self):
        """default() fires when no case in the match group matched."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.on_finished()
        (
            fr.match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .case("MRI")
            .create_record("seg-mri")
            .default()
            .create_record("seg-unknown")
        )

        assert len(fr.conditions) == 3
        # Last condition is the default (is_else=True with match_group)
        assert fr.conditions[2].is_else is True
        assert fr.conditions[2].match_group is not None
        assert fr.conditions[2].match_group == fr.conditions[0].match_group

    def test_default_skipped_when_case_matches(self):
        """default() condition structure: no condition when no guard."""
        F = Field()
        fr = FlowRecord("test-type")
        (
            fr.match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .default()
            .create_record("seg-default")
        )

        # default without guard has condition=None
        assert fr.conditions[1].condition is None
        assert fr.conditions[1].is_else is True

    def test_default_guard_false_nothing_fires(self):
        """default() carries guard from if_record() so it doesn't fire when guard is False."""
        F = Field()
        fr = FlowRecord("test-type")
        (
            fr.if_record(F.is_good == True)  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .default()
            .create_record("seg-default")
        )

        # default condition carries the guard
        assert fr.conditions[1].is_else is True
        assert fr.conditions[1].condition is not None  # has guard

    def test_default_no_guard_fires(self):
        """default() without guard has condition=None (always fires when no case matched)."""
        F = Field()
        fr = FlowRecord("test-type")
        (
            fr.match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .default()
            .create_record("seg-default")
        )

        assert fr.conditions[1].condition is None

    def test_default_without_match_raises(self):
        """default() without preceding match() raises ValueError."""
        fr = FlowRecord("test-type")
        with pytest.raises(ValueError, match=r"default.*must be called after match"):
            fr.default()

    def test_default_validates_ok(self):
        """validate() passes for default() with actions (is_else is exempt)."""
        F = Field()
        fr = FlowRecord("test-type")
        fr.on_finished()
        (
            fr.match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .default()
            .create_record("seg-default")
        )
        assert fr.validate() is True

    @pytest.mark.asyncio
    async def test_engine_match_case(self):
        """Engine stop-on-first-match: only the first matching case fires."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(
            return_value=make_record_read("seg-mri", record_id=99)
        )

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("first-check")
        flow_def.on_status("finished")
        (
            flow_def.if_record(F.is_good == True)  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct-single", "seg-ct-archive")
            .case("MRI")
            .create_record("seg-mri-single")
            .case("CT-AG")
            .create_record("seg-ctag-single")
        )

        engine.register_flow(flow_def)

        # Trigger with study_type=MRI
        test_record = make_record_read(
            "first-check",
            record_id=100,
            status=RecordStatus.finished,
            data={"is_good": True, "study_type": "MRI"},
        )

        await engine.handle_record_status_change(test_record)

        # Only MRI branch should fire (1 record)
        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "seg-mri-single"

    @pytest.mark.asyncio
    async def test_engine_match_case_no_match(self):
        """Engine does nothing when no case matches."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("first-check")
        flow_def.on_status("finished")
        (
            flow_def.if_record(F.is_good == True)  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
        )

        engine.register_flow(flow_def)

        # Trigger with study_type=PET (no matching case)
        test_record = make_record_read(
            "first-check",
            record_id=100,
            status=RecordStatus.finished,
            data={"is_good": True, "study_type": "PET"},
        )

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 0

    @pytest.mark.asyncio
    async def test_engine_default_branch(self):
        """Engine default branch fires when no case matches."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(
            return_value=make_record_read("seg-default", record_id=99)
        )

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("first-check")
        flow_def.on_status("finished")
        (
            flow_def.match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .case("MRI")
            .create_record("seg-mri")
            .default()
            .create_record("seg-default")
        )

        engine.register_flow(flow_def)

        # Trigger with study_type=PET (no matching case → default fires)
        test_record = make_record_read(
            "first-check",
            record_id=100,
            status=RecordStatus.finished,
            data={"study_type": "PET"},
        )

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "seg-default"

    @pytest.mark.asyncio
    async def test_engine_default_skipped_when_case_matches(self):
        """Engine default branch does NOT fire when a case matches."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(return_value=make_record_read("seg-ct", record_id=99))

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("first-check")
        flow_def.on_status("finished")
        (
            flow_def.match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .case("MRI")
            .create_record("seg-mri")
            .default()
            .create_record("seg-default")
        )

        engine.register_flow(flow_def)

        # Trigger with study_type=CT → CT branch fires, default skipped
        test_record = make_record_read(
            "first-check",
            record_id=100,
            status=RecordStatus.finished,
            data={"study_type": "CT"},
        )

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "seg-ct"

    @pytest.mark.asyncio
    async def test_engine_default_guard_false_nothing_fires(self):
        """When guard is False, neither cases nor default fire."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("first-check")
        flow_def.on_status("finished")
        (
            flow_def.if_record(F.is_good == True)  # noqa: E712
            .match(F.study_type)
            .case("CT")
            .create_record("seg-ct")
            .default()
            .create_record("seg-default")
        )

        engine.register_flow(flow_def)

        # Guard is False (is_good=False), study_type=PET → nothing fires
        test_record = make_record_read(
            "first-check",
            record_id=100,
            status=RecordStatus.finished,
            data={"is_good": False, "study_type": "PET"},
        )

        await engine.handle_record_status_change(test_record)

        assert mock_client.create_record.call_count == 0

    @pytest.mark.asyncio
    async def test_engine_stop_on_first_match(self):
        """Engine stops evaluating cases after the first match in a group."""
        from unittest.mock import AsyncMock

        from clarinet.services.recordflow.engine import RecordFlowEngine

        mock_client = AsyncMock()
        mock_client.find_records = AsyncMock(return_value=[])
        mock_client.create_record = AsyncMock(return_value=make_record_read("seg", record_id=99))

        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow_def = FlowRecord("test-type")
        flow_def.on_status("finished")
        (
            flow_def.match(F.score)
            .case(10)
            .create_record("first-match")
            .case(10)
            .create_record("second-match")
        )

        engine.register_flow(flow_def)

        test_record = make_record_read(
            "test",
            record_id=100,
            status=RecordStatus.finished,
            data={"score": 10},
        )

        await engine.handle_record_status_change(test_record)

        # Only the first matching case should fire
        assert mock_client.create_record.call_count == 1
        call_args = mock_client.create_record.call_args[0][0]
        assert call_args.record_type_name == "first-match"


class TestIsExpectedConflict:
    """Tests for _is_expected_conflict helper in action_handlers."""

    def test_record_limit_reached_is_expected(self):
        from clarinet.client import ClarinetAPIError
        from clarinet.services.recordflow.action_handlers import _is_expected_conflict

        exc = ClarinetAPIError(
            "API error: 409",
            status_code=409,
            detail={
                "detail": "The maximum records limit (1 of 1) is reached",
                "code": "RECORD_LIMIT_REACHED",
            },
        )
        assert _is_expected_conflict(exc) is True

    def test_unique_per_user_is_expected(self):
        from clarinet.client import ClarinetAPIError
        from clarinet.services.recordflow.action_handlers import _is_expected_conflict

        exc = ClarinetAPIError(
            "API error: 409",
            status_code=409,
            detail={"detail": "User already has a record", "code": "UNIQUE_PER_USER"},
        )
        assert _is_expected_conflict(exc) is True

    def test_unknown_409_is_not_expected(self):
        from clarinet.client import ClarinetAPIError
        from clarinet.services.recordflow.action_handlers import _is_expected_conflict

        exc = ClarinetAPIError(
            "API error: 409",
            status_code=409,
            detail={"detail": "Resource already exists"},
        )
        assert _is_expected_conflict(exc) is False

    def test_non_409_is_not_expected(self):
        from clarinet.client import ClarinetAPIError
        from clarinet.services.recordflow.action_handlers import _is_expected_conflict

        exc = ClarinetAPIError("API error: 500", status_code=500, detail="Server error")
        assert _is_expected_conflict(exc) is False

    def test_non_api_error_is_not_expected(self):
        from clarinet.services.recordflow.action_handlers import _is_expected_conflict

        assert _is_expected_conflict(ValueError("oops")) is False
