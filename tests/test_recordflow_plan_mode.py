"""Unit tests for the dry-run plan mode of :class:`RecordFlowEngine`.

The plan-mode path replaces real action execution with collection of
:class:`ActionPreview` objects; no `ClarinetClient` mutations should be made.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.patient import PatientBase
from clarinet.models.record import RecordRead, RecordTypeBase
from clarinet.models.study import StudyBase
from clarinet.services.recordflow import (
    ENTITY_REGISTRY,
    FILE_REGISTRY,
    RECORD_REGISTRY,
    ActionPreview,
    Field,
    FlowFileRecord,
    FlowRecord,
)
from clarinet.services.recordflow.engine import RecordFlowEngine


def _make_record(
    name: str,
    *,
    record_id: int = 1,
    status: RecordStatus = RecordStatus.pending,
    data: dict | None = None,
) -> RecordRead:
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
    """Clear flow registries before/after each test (mirrors test_recordflow_dsl)."""
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


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.find_records = AsyncMock(return_value=[])
    return client


class TestPlanRecordStatusChange:
    @pytest.mark.asyncio
    async def test_collects_create_record_preview(self, mock_client):
        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.add_record("output-type")
        engine.register_flow(flow)

        record = _make_record("trigger-type", status=RecordStatus.finished)

        plan = await engine.plan_record_status_change(record)

        assert len(plan) == 1
        assert isinstance(plan[0], ActionPreview)
        assert plan[0].action_type == "create_record"
        assert plan[0].target == "output-type"
        assert plan[0].trigger_record_id == record.id
        assert plan[0].trigger_record_type == "trigger-type"
        # Side effects must NOT have been performed
        mock_client.create_record.assert_not_called()
        mock_client.update_record_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_status_override_plans_for_target_status(self, mock_client):
        """Planner pretends record is in target status without mutating input."""
        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.add_record("output-from-finished")
        engine.register_flow(flow)

        # Real status is "pending"; ask planner what happens for "finished"
        record = _make_record("trigger-type", status=RecordStatus.pending)
        original_status = record.status

        plan = await engine.plan_record_status_change(record, status_override="finished")

        assert len(plan) == 1
        assert plan[0].target == "output-from-finished"
        # Input record must not be mutated
        assert record.status == original_status

    @pytest.mark.asyncio
    async def test_status_mismatch_returns_empty_plan(self, mock_client):
        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.add_record("output-type")
        engine.register_flow(flow)

        record = _make_record("trigger-type", status=RecordStatus.pending)

        plan = await engine.plan_record_status_change(record)
        assert plan == []

    @pytest.mark.asyncio
    async def test_pipeline_action_preview(self, mock_client):
        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.pipeline("seg_pipeline", quality="high")
        engine.register_flow(flow)

        record = _make_record("trigger-type", status=RecordStatus.finished)

        plan = await engine.plan_record_status_change(record)

        assert len(plan) == 1
        preview = plan[0]
        assert preview.action_type == "pipeline"
        assert preview.target == "seg_pipeline"
        assert preview.details["pipeline_name"] == "seg_pipeline"
        assert preview.details["extra_payload"] == {"quality": "high"}

    @pytest.mark.asyncio
    async def test_match_case_only_first_branch_planned(self, mock_client):
        """Stop-on-first-match semantics also apply in plan mode."""
        engine = RecordFlowEngine(mock_client)

        F = Field()
        flow = FlowRecord("trigger-type")
        flow.on_status("finished")
        flow.match(F.study_type)
        flow.case("CT")
        flow.add_record("seg_CT")
        flow.case("MRI")
        flow.add_record("seg_MRI")
        flow.default()
        flow.add_record("seg_unknown")
        engine.register_flow(flow)

        record = _make_record(
            "trigger-type",
            status=RecordStatus.finished,
            data={"study_type": "CT"},
        )

        plan = await engine.plan_record_status_change(record)
        targets = [p.target for p in plan]
        assert targets == ["seg_CT"]


class TestPlanRecordDataUpdate:
    @pytest.mark.asyncio
    async def test_only_data_update_flow_planned(self, mock_client):
        engine = RecordFlowEngine(mock_client)

        flow_status = FlowRecord("rec-type")
        flow_status.on_status("finished")
        flow_status.add_record("from-status")

        flow_data = FlowRecord("rec-type")
        flow_data.on_data_update()
        flow_data.add_record("from-data-update")

        engine.register_flow(flow_status)
        engine.register_flow(flow_data)

        record = _make_record("rec-type", status=RecordStatus.finished)

        plan = await engine.plan_record_data_update(record)
        assert len(plan) == 1
        assert plan[0].target == "from-data-update"


class TestPlanFileUpdate:
    @pytest.mark.asyncio
    async def test_invalidate_records_preview(self, mock_client):
        engine = RecordFlowEngine(mock_client)

        flow = FlowFileRecord("master_model")
        flow.on_update()
        flow.invalidate_all_records("child_a", "child_b", mode="hard")
        engine.register_flow(flow)

        plan = await engine.plan_file_update("master_model", patient_id="PAT001")
        assert len(plan) == 1
        preview = plan[0]
        assert preview.action_type == "invalidate_records"
        assert preview.details["record_type_names"] == ["child_a", "child_b"]
        assert preview.details["mode"] == "hard"
        assert preview.file_name == "master_model"
        # Side effect must NOT happen
        mock_client.invalidate_record.assert_not_called()


class TestPlanEntityCreated:
    @pytest.mark.asyncio
    async def test_series_created_preview(self, mock_client):
        from clarinet.services.recordflow.flow_record import series

        engine = RecordFlowEngine(mock_client)

        flow = series().on_created().add_record("series_markup")
        engine.register_flow(flow)

        plan = await engine.plan_entity_created(
            "series",
            patient_id="PAT001",
            study_uid="1.2.3",
            series_uid="1.2.3.4",
        )
        assert len(plan) == 1
        assert plan[0].target == "series_markup"
        assert plan[0].patient_id == "PAT001"
        assert plan[0].study_uid == "1.2.3"
        assert plan[0].series_uid == "1.2.3.4"


class TestPlanModeIsolation:
    """Plan mode must not leave any side effects on the engine or client."""

    @pytest.mark.asyncio
    async def test_no_mutations_on_real_client_methods(self, mock_client):
        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("rec-type")
        flow.on_status("finished")
        flow.add_record("out_a")
        flow.update_record("rec-type", status="finished")
        flow.invalidate_records("rec-type")
        engine.register_flow(flow)

        record = _make_record("rec-type", status=RecordStatus.finished)
        plan = await engine.plan_record_status_change(record)

        # All three actions are previewed, not executed
        types = [p.action_type for p in plan]
        assert types == ["create_record", "update_record", "invalidate_records"]
        mock_client.create_record.assert_not_called()
        mock_client.update_record_status.assert_not_called()
        mock_client.invalidate_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_status_change_still_executes_when_no_collector(self, mock_client):
        """The plumbing must not change normal execution semantics."""
        engine = RecordFlowEngine(mock_client)

        flow = FlowRecord("rec-type")
        flow.on_status("finished")
        flow.add_record("output")
        engine.register_flow(flow)

        record = _make_record("rec-type", status=RecordStatus.finished)
        await engine.handle_record_status_change(record)

        assert mock_client.create_record.call_count == 1
