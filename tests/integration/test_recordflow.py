"""Integration tests for RecordFlow engine with real DB and API.

Uses clarinet_client fixture (real HTTP client → FastAPI app → in-memory SQLite).
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.api.app import app
from clarinet.client import ClarinetClient
from clarinet.models.base import DicomQueryLevel, RecordStatus
from clarinet.models.patient import Patient
from clarinet.models.record import RecordCreate, RecordRead, RecordType
from clarinet.models.study import Series, Study
from clarinet.services.recordflow import FlowRecord, FlowResult, RecordFlowEngine
from clarinet.services.recordflow.flow_file import FILE_REGISTRY
from clarinet.services.recordflow.flow_record import ENTITY_REGISTRY, RECORD_REGISTRY
from clarinet.utils.logger import logger


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global FlowRecord registries between tests."""
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    FILE_REGISTRY.clear()


@pytest_asyncio.fixture(autouse=True)
async def _auth_override(test_session: AsyncSession):
    """Bypass auth for recordflow integration tests."""
    from uuid import uuid4

    from clarinet.api.app import app
    from clarinet.api.auth_config import current_active_user, current_superuser
    from clarinet.models.user import User
    from clarinet.utils.auth import get_password_hash

    mock_user = User(
        id=uuid4(),
        email="flow_test@test.com",
        hashed_password=get_password_hash("mock"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_session.add(mock_user)
    await test_session.commit()
    await test_session.refresh(mock_user)

    app.dependency_overrides[current_active_user] = lambda: mock_user
    app.dependency_overrides[current_superuser] = lambda: mock_user
    yield
    app.dependency_overrides.pop(current_active_user, None)
    app.dependency_overrides.pop(current_superuser, None)


@pytest_asyncio.fixture
async def record_types(test_session: AsyncSession) -> dict[str, RecordType]:
    """Create record types used by flow tests."""
    types = {}
    study_level_types = [
        "doctor-report",
        "ai-analysis",
        "expert-check",
        "confirm-birads",
        "parent-model",
        "child-analysis",
        "first-check",
    ]
    series_level_types = ["series-markup"]
    for name in study_level_types:
        rt = RecordType(name=name, level=DicomQueryLevel.STUDY, unique_per_user=False)
        test_session.add(rt)
        types[name] = rt
    for name in series_level_types:
        rt = RecordType(name=name, level=DicomQueryLevel.SERIES, unique_per_user=False)
        test_session.add(rt)
        types[name] = rt
    await test_session.commit()
    for rt in types.values():
        await test_session.refresh(rt)
    return types


@pytest_asyncio.fixture
async def flow_engine(clarinet_client: ClarinetClient) -> RecordFlowEngine:
    """Create a RecordFlowEngine backed by the test client."""
    return RecordFlowEngine(clarinet_client)


async def _create_record_via_client(
    clarinet_client: ClarinetClient,
    record_type_name: str,
    patient_id: str,
    study_uid: str,
    status: RecordStatus = RecordStatus.pending,
    data: dict | None = None,
) -> RecordRead:
    """Create a record through the API and optionally set data/status."""
    record_create = RecordCreate(
        record_type_name=record_type_name,
        patient_id=patient_id,
        study_uid=study_uid,
    )
    created = await clarinet_client.create_record(record_create)

    # Submit data if provided
    if data is not None:
        created = await clarinet_client.submit_record_data(created.id, data)

    # Update status if not pending
    if status != RecordStatus.pending:
        created = await clarinet_client.update_record_status(created.id, status)

    return created


class TestRecordFlowIntegration:
    """Integration tests for RecordFlowEngine with real API."""

    @pytest.mark.asyncio
    async def test_unconditional_flow_creates_record(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Unconditional flow on status=finished creates a new record."""
        # Define flow: doctor_report finished → create ai_analysis
        flow = FlowRecord("doctor-report")
        flow.on_status("finished").add_record("ai-analysis")
        flow_engine.register_flow(flow)

        # Create trigger record
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Execute flow
        await flow_engine.handle_record_status_change(trigger)

        # Verify: ai_analysis record was created
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai-analysis",
        )
        assert len(records) == 1
        assert records[0].record_type.name == "ai-analysis"

    @pytest.mark.asyncio
    async def test_conditional_flow_true(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Conditional flow executes when condition is True (confidence < 70)."""
        # Define flow
        flow = FlowRecord("doctor-report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor-report", ["confidence"]) < 70).add_record("expert-check")
        flow_engine.register_flow(flow)

        # Create trigger record with confidence=50 (< 70 → True)
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"confidence": 50},
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="expert-check",
        )
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_conditional_flow_false(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Conditional flow does NOT execute when condition is False (confidence >= 70)."""
        flow = FlowRecord("doctor-report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor-report", ["confidence"]) < 70).add_record("expert-check")
        flow_engine.register_flow(flow)

        # confidence=90 → condition False
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"confidence": 90},
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="expert-check",
        )
        assert len(records) == 0

    @pytest.mark.asyncio
    async def test_else_branch(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Else branch executes when if_ condition is False."""
        flow = FlowRecord("doctor-report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor-report", ["confidence"]) < 70).add_record("expert-check")
        flow.else_().add_record("ai-analysis")
        flow_engine.register_flow(flow)

        # confidence=90 → if_ False → else_ executes
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"confidence": 90},
        )

        await flow_engine.handle_record_status_change(trigger)

        # expert_check should NOT exist
        expert = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="expert-check",
        )
        assert len(expert) == 0

        # ai_analysis SHOULD exist (else branch)
        ai = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai-analysis",
        )
        assert len(ai) == 1

    @pytest.mark.asyncio
    async def test_cross_record_comparison(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Flow compares data from two different record types."""
        flow = FlowRecord("doctor-report")
        flow.on_status("finished")
        flow.if_(
            FlowResult("doctor-report", ["diagnosis"]) != FlowResult("ai-analysis", ["diagnosis"])
        ).add_record("confirm-birads")
        flow_engine.register_flow(flow)

        # Create ai_analysis first (context record)
        await _create_record_via_client(
            clarinet_client,
            "ai-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"diagnosis": "benign"},
        )

        # Create doctor_report with different diagnosis
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"diagnosis": "malignant"},
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="confirm-birads",
        )
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_update_record_action(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """update_record() changes status of an existing record in context."""
        flow = FlowRecord("doctor-report")
        flow.on_status("finished").update_record("ai-analysis", status="finished")
        flow_engine.register_flow(flow)

        # Create ai_analysis (pending)
        await _create_record_via_client(
            clarinet_client,
            "ai-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.pending,
        )

        # Create trigger
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        await flow_engine.handle_record_status_change(trigger)

        # Verify ai_analysis status changed
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai-analysis",
        )
        assert len(records) == 1
        assert records[0].status == RecordStatus.finished

    @pytest.mark.asyncio
    async def test_no_trigger_on_wrong_status(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Flow does not trigger when record status doesn't match trigger."""
        flow = FlowRecord("doctor-report")
        flow.on_status("finished").add_record("ai-analysis")
        flow_engine.register_flow(flow)

        # Create record with status=pending (not "finished")
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.pending,
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai-analysis",
        )
        assert len(records) == 0

    @pytest.mark.asyncio
    async def test_custom_function_call(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """call() executes a custom async function with record context."""
        call_log: list[dict] = []

        async def custom_handler(record, context, client, **kwargs):
            call_log.append(
                {
                    "record_id": record.id,
                    "record_type": record.record_type.name,
                    "context_keys": list(context.keys()),
                }
            )

        flow = FlowRecord("doctor-report")
        flow.on_status("finished").call(custom_handler)
        flow_engine.register_flow(flow)

        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        await flow_engine.handle_record_status_change(trigger)

        assert len(call_log) == 1
        assert call_log[0]["record_type"] == "doctor-report"
        assert isinstance(call_log[0]["record_id"], int)


class TestRecordFlowRuntime:
    """Tests for the full runtime chain: PATCH /status → background task → engine → new record.

    Verifies that app.state.recordflow_engine is triggered by the API endpoint,
    the same way it works in production.
    """

    @pytest_asyncio.fixture
    async def app_with_engine(
        self,
        clarinet_client: ClarinetClient,
    ) -> AsyncGenerator[RecordFlowEngine]:
        """Install RecordFlowEngine into app.state, clean up after test."""
        engine = RecordFlowEngine(clarinet_client)
        app.state.recordflow_engine = engine
        yield engine
        for task in engine._background_tasks:
            task.cancel()
        app.state.recordflow_engine = None

    @pytest.mark.asyncio
    async def test_status_change_triggers_flow_via_api(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        app_with_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """PATCH /records/{id}/status triggers engine and creates a new record."""
        # Register flow: doctor_report finished → create ai_analysis
        flow = FlowRecord("doctor-report")
        flow.on_status("finished").add_record("ai-analysis")
        app_with_engine.register_flow(flow)

        # Create a record via API (status=pending by default)
        create_resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-report",
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
            },
        )
        assert create_resp.status_code == 201
        record_id = create_resp.json()["id"]

        # Change status via PATCH — this triggers background task with engine
        patch_resp = await client.patch(
            f"/api/records/{record_id}/status",
            params={"record_status": "finished"},
        )
        assert patch_resp.status_code == 200

        # Verify: ai_analysis was created by the engine via background task
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai-analysis",
        )
        assert len(records) == 1
        assert records[0].record_type.name == "ai-analysis"

    @pytest.mark.asyncio
    async def test_conditional_flow_via_api(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        app_with_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """PATCH /status triggers conditional flow — condition True creates record."""
        flow = FlowRecord("doctor-report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor-report", ["confidence"]) < 70).add_record("expert-check")
        app_with_engine.register_flow(flow)

        # Create record
        create_resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-report",
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
            },
        )
        record_id = create_resp.json()["id"]

        # Submit data with low confidence
        await client.post(
            f"/api/records/{record_id}/data",
            json={"confidence": 50},
        )

        # Change status → triggers flow
        patch_resp = await client.patch(
            f"/api/records/{record_id}/status",
            params={"record_status": "finished"},
        )
        assert patch_resp.status_code == 200

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="expert-check",
        )
        assert len(records) == 1

    @pytest.mark.asyncio
    async def test_no_engine_means_no_flow(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Without engine in app.state, status change does NOT trigger any flow."""
        # Ensure no engine is set (default state)
        app.state.recordflow_engine = None

        create_resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor-report",
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
            },
        )
        record_id = create_resp.json()["id"]

        patch_resp = await client.patch(
            f"/api/records/{record_id}/status",
            params={"record_status": "finished"},
        )
        assert patch_resp.status_code == 200

        # No flow engine → no ai_analysis created
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai-analysis",
        )
        assert len(records) == 0


class TestRecordFlowInvalidation:
    """Integration tests for record invalidation flow with real DB."""

    @pytest.mark.asyncio
    async def test_hard_invalidate_resets_to_pending(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Hard invalidation resets child record to pending status."""
        # Define flow: parent_model on_data_update → invalidate child_analysis (hard)
        flow = FlowRecord("parent-model")
        flow.on_data_update().invalidate_records("child-analysis", mode="hard")
        flow_engine.register_flow(flow)

        # Create parent record (finished with data)
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"model_version": "v1"},
        )

        # Create child record (finished)
        await _create_record_via_client(
            clarinet_client,
            "child-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update flow
        await flow_engine.handle_record_data_update(parent_record)

        # Verify child is now pending and has invalidation info
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child-analysis",
        )
        assert len(records) == 1
        assert records[0].status == RecordStatus.pending
        assert records[0].context_info is not None
        assert "Invalidated by record" in records[0].context_info

    @pytest.mark.asyncio
    async def test_soft_invalidate_keeps_status(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Soft invalidation keeps status but updates context_info."""
        # Define flow: parent_model on_data_update → invalidate child_analysis (soft)
        flow = FlowRecord("parent-model")
        flow.on_data_update().invalidate_records("child-analysis", mode="soft")
        flow_engine.register_flow(flow)

        # Create parent record (finished with data)
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"model_version": "v1"},
        )

        # Create child record (finished)
        await _create_record_via_client(
            clarinet_client,
            "child-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update flow
        await flow_engine.handle_record_data_update(parent_record)

        # Verify child status unchanged but context_info updated
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child-analysis",
        )
        assert len(records) == 1
        assert records[0].status == RecordStatus.finished
        assert records[0].context_info is not None
        assert "Invalidated by record" in records[0].context_info

    @pytest.mark.asyncio
    async def test_invalidate_skips_source_record(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Invalidation skips the source record when invalidating same type."""
        # Define flow: parent_model on_data_update → invalidate parent_model (self-invalidation)
        flow = FlowRecord("parent-model")
        flow.on_data_update().invalidate_records("parent-model", mode="hard")
        flow_engine.register_flow(flow)

        # Create two parent_model records
        first_record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"version": "1"},
        )

        second_record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"version": "2"},
        )

        # Trigger data update on first record
        await flow_engine.handle_record_data_update(first_record)

        # Verify: first record NOT invalidated, second record IS invalidated
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="parent-model",
        )
        assert len(records) == 2

        first_updated = next(r for r in records if r.id == first_record.id)
        second_updated = next(r for r in records if r.id == second_record.id)

        # First (source) should remain finished
        assert first_updated.status == RecordStatus.finished
        # Second should be invalidated (reset to pending)
        assert second_updated.status == RecordStatus.pending
        assert "Invalidated by record" in (second_updated.context_info or "")

    @pytest.mark.asyncio
    async def test_invalidate_with_callback(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Invalidation with callback executes the callback function."""
        call_log: list[dict] = []

        async def invalidation_callback(record, source_record, client, **kwargs):
            call_log.append(
                {
                    "record_id": record.id,
                    "record_type": record.record_type.name,
                    "source_record_id": source_record.id,
                }
            )

        # Define flow with callback
        flow = FlowRecord("parent-model")
        flow.on_data_update().invalidate_records(
            "child-analysis", mode="hard", callback=invalidation_callback
        )
        flow_engine.register_flow(flow)

        # Create parent and child
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"data": "test"},
        )

        await _create_record_via_client(
            clarinet_client,
            "child-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update
        await flow_engine.handle_record_data_update(parent_record)

        # Verify callback was called with the target (child) record
        assert len(call_log) == 1
        assert call_log[0]["record_type"] == "child-analysis"
        assert call_log[0]["source_record_id"] == parent_record.id

    @pytest.mark.asyncio
    async def test_invalidate_multiple_types(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """Invalidation can target multiple record types simultaneously."""
        # Define flow: parent_model on_data_update → invalidate child_analysis AND ai_analysis
        flow = FlowRecord("parent-model")
        flow.on_data_update().invalidate_records("child-analysis", "ai-analysis", mode="hard")
        flow_engine.register_flow(flow)

        # Create parent
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"data": "test"},
        )

        # Create child_analysis (finished)
        await _create_record_via_client(
            clarinet_client,
            "child-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Create ai_analysis (finished)
        await _create_record_via_client(
            clarinet_client,
            "ai-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update
        await flow_engine.handle_record_data_update(parent_record)

        # Verify both child_analysis and ai_analysis are invalidated
        child_records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child-analysis",
        )
        assert len(child_records) == 1
        assert child_records[0].status == RecordStatus.pending

        ai_records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai-analysis",
        )
        assert len(ai_records) == 1
        assert ai_records[0].status == RecordStatus.pending


class TestRecordFlowInvalidationRuntime:
    """Tests for invalidation triggered through API (PATCH /data → engine)."""

    @pytest_asyncio.fixture
    async def app_with_engine(
        self,
        clarinet_client: ClarinetClient,
    ) -> AsyncGenerator[RecordFlowEngine]:
        """Install RecordFlowEngine into app.state, clean up after test."""
        engine = RecordFlowEngine(clarinet_client)
        app.state.recordflow_engine = engine
        yield engine
        for task in engine._background_tasks:
            task.cancel()
        app.state.recordflow_engine = None

    @pytest.mark.asyncio
    async def test_data_update_triggers_invalidation_via_api(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        app_with_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """PATCH /records/{id}/data triggers invalidation through engine."""
        # Register flow: parent_model on_data_update → invalidate child_analysis
        flow = FlowRecord("parent-model")
        flow.on_data_update().invalidate_records("child-analysis", mode="hard")
        app_with_engine.register_flow(flow)

        # Create parent_model via API
        create_resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "parent-model",
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
            },
        )
        assert create_resp.status_code == 201
        parent_id = create_resp.json()["id"]

        # Submit initial data for parent
        await client.post(
            f"/api/records/{parent_id}/data",
            json={"initial": "data"},
        )

        # Create child_analysis and set to finished
        child_create = await client.post(
            "/api/records/",
            json={
                "record_type_name": "child-analysis",
                "patient_id": test_patient.id,
                "study_uid": test_study.study_uid,
            },
        )
        child_id = child_create.json()["id"]

        # Set child to finished status
        await client.patch(
            f"/api/records/{child_id}/status",
            params={"record_status": "finished"},
        )

        # Update parent data via PATCH — this triggers invalidation
        patch_resp = await client.patch(
            f"/api/records/{parent_id}/data",
            json={"updated": "data"},
        )
        assert patch_resp.status_code == 200

        # Verify child_analysis is now pending (invalidated)
        child_records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child-analysis",
        )
        assert len(child_records) == 1
        assert child_records[0].status == RecordStatus.pending
        assert "Invalidated by record" in (child_records[0].context_info or "")


class TestInvalidateEndpoint:
    """Tests for the direct POST /records/{id}/invalidate endpoint."""

    @pytest.mark.asyncio
    async def test_invalidate_hard_mode(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """POST /invalidate with hard mode resets status to pending."""
        # Create a finished record
        record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"some": "data"},
        )

        # Call invalidate endpoint
        resp = await client.post(
            f"/api/records/{record.id}/invalidate",
            json={"mode": "hard", "reason": "test reason"},
        )
        assert resp.status_code == 200

        # Verify record is now pending with reason in context_info
        updated_record = await clarinet_client.get_record(record.id)
        assert updated_record.status == RecordStatus.pending
        assert updated_record.context_info is not None
        assert "test reason" in updated_record.context_info

    @pytest.mark.asyncio
    async def test_invalidate_soft_mode(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """POST /invalidate with soft mode keeps status but appends context_info."""
        # Create a finished record with existing context_info
        record = await _create_record_via_client(
            clarinet_client,
            "parent-model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"some": "data"},
        )

        # Add existing context by updating it manually
        await client.patch(
            f"/api/records/{record.id}/data",
            json={"existing": "context"},
        )

        # Call invalidate endpoint with soft mode
        resp = await client.post(
            f"/api/records/{record.id}/invalidate",
            json={"mode": "soft", "reason": "soft reason"},
        )
        assert resp.status_code == 200

        # Verify status unchanged, context_info appended
        updated_record = await clarinet_client.get_record(record.id)
        assert updated_record.status == RecordStatus.finished
        assert updated_record.context_info is not None
        assert "soft reason" in updated_record.context_info


class TestEntityFlowIntegration:
    """Integration tests for entity creation flows."""

    @pytest.mark.asyncio
    async def test_entity_flow_creates_record_on_series(
        self,
        clarinet_client: ClarinetClient,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
        test_session: AsyncSession,
    ):
        """Engine entity flow creates a record when triggered for a series."""
        # Series must exist in DB before engine creates a Record referencing it
        series = Series(
            series_uid="1.2.3.4.5.6.7.8.9.10",
            study_uid=test_study.study_uid,
            series_number=1,
        )
        test_session.add(series)
        await test_session.commit()

        engine = RecordFlowEngine(clarinet_client)

        fr = FlowRecord("series", entity_trigger="series")
        fr.add_record("series-markup")
        engine.register_flow(fr)

        await engine.handle_entity_created(
            "series",
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid="1.2.3.4.5.6.7.8.9.10",
        )

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="series-markup",
        )
        assert len(records) == 1
        assert records[0].record_type.name == "series-markup"
        assert records[0].series_uid == "1.2.3.4.5.6.7.8.9.10"


class TestEntityFlowRuntime:
    """Tests for entity flows triggered through API endpoints."""

    @pytest_asyncio.fixture
    async def app_with_engine(
        self,
        clarinet_client: ClarinetClient,
    ) -> AsyncGenerator[RecordFlowEngine]:
        """Install RecordFlowEngine into app.state, clean up after test."""
        engine = RecordFlowEngine(clarinet_client)
        app.state.recordflow_engine = engine
        yield engine
        for task in engine._background_tasks:
            task.cancel()
        app.state.recordflow_engine = None

    @pytest.mark.asyncio
    async def test_post_series_triggers_entity_flow(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        app_with_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """POST /series triggers entity flow and creates a record."""
        fr = FlowRecord("series", entity_trigger="series")
        fr.add_record("series-markup")
        app_with_engine.register_flow(fr)

        # Create series via API
        resp = await client.post(
            "/api/series",
            json={
                "series_uid": "9.8.7.6.5.4.3.2.1",
                "series_number": 1,
                "study_uid": test_study.study_uid,
            },
        )
        assert resp.status_code == 201

        # Wait for fire-and-forget background tasks to finish before querying,
        # because test_session is shared and doesn't support concurrent access.
        for task in list(app_with_engine._background_tasks):
            try:
                await task
            except Exception:
                logger.opt(exception=True).warning("Background entity flow task failed")

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="series-markup",
        )
        assert len(records) == 1
        assert records[0].series_uid == "9.8.7.6.5.4.3.2.1"

    @pytest.mark.asyncio
    async def test_study_entity_flow_creates_record(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        app_with_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_patient: Patient,
    ):
        """study().on_created() creates record after commit — no FK violation.

        Regression: engine.fire() ran before commit, causing FK violation
        when background task tried to create a record referencing uncommitted study.
        """
        fr = FlowRecord("study", entity_trigger="study")
        fr.add_record("first-check")
        app_with_engine.register_flow(fr)

        resp = await client.post(
            "/api/studies",
            json={
                "study_uid": "1.2.3.99.88.77.66",
                "date": "2026-01-01",
                "patient_id": test_patient.id,
            },
        )
        assert resp.status_code == 201

        # Wait for fire-and-forget background tasks to finish before querying,
        # because test_session is shared and doesn't support concurrent access.
        for task in list(app_with_engine._background_tasks):
            try:
                await task
            except Exception:
                logger.opt(exception=True).warning("Background entity flow task failed")

        records = await clarinet_client.find_records(
            study_uid="1.2.3.99.88.77.66",
            record_type_name="first-check",
        )
        assert len(records) == 1


class TestLazyAuthentication:
    """Tests for lazy authentication in RecordFlowEngine."""

    @pytest.mark.asyncio
    async def test_engine_authenticates_lazily_on_entity_created(
        self,
        clarinet_client: ClarinetClient,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
        test_session: AsyncSession,
    ):
        """Engine calls _ensure_authenticated() and creates record without prior login."""
        # Series must exist in DB before engine creates a Record referencing it
        series = Series(
            series_uid="1.2.3.99.88.77",
            study_uid=test_study.study_uid,
            series_number=1,
        )
        test_session.add(series)
        await test_session.commit()

        # Client is NOT authenticated (auto_login=False, no login() called)
        assert clarinet_client._authenticated is False

        # Give the client credentials so login() can succeed
        clarinet_client.username = "flow_test@test.com"
        clarinet_client.password = "mock"

        engine = RecordFlowEngine(clarinet_client)

        fr = FlowRecord("series", entity_trigger="series")
        fr.add_record("series-markup")
        engine.register_flow(fr)

        # This should trigger _ensure_authenticated → login → create record
        await engine.handle_entity_created(
            "series",
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid="1.2.3.99.88.77",
        )

        # Client should now be authenticated
        assert clarinet_client._authenticated is True

        # Record should have been created
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="series-markup",
        )
        assert len(records) == 1
        assert records[0].series_uid == "1.2.3.99.88.77"


class TestFileFlowIntegration:
    """Integration tests for file update → invalidation chain."""

    @pytest.mark.asyncio
    async def test_file_update_invalidates_records(
        self,
        clarinet_client: ClarinetClient,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """handle_file_update() invalidates matching records via API."""
        from clarinet.services.recordflow.flow_file import FlowFileRecord

        engine = RecordFlowEngine(clarinet_client)

        # Register file flow: master_model change → invalidate child_analysis
        fr = FlowFileRecord("master_model")
        fr.on_update().invalidate_all_records("child-analysis", mode="hard")
        engine.register_flow(fr)

        # Create a finished child_analysis record
        await _create_record_via_client(
            clarinet_client,
            "child-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger file update
        await engine.handle_file_update("master_model", test_patient.id)

        # Verify child_analysis is now pending (invalidated)
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child-analysis",
        )
        assert len(records) == 1
        assert records[0].status == RecordStatus.pending
        assert records[0].context_info is not None
        assert "file change" in records[0].context_info

    @pytest.mark.asyncio
    async def test_file_event_endpoint(
        self,
        client: AsyncClient,
        clarinet_client: ClarinetClient,
        record_types: dict[str, RecordType],
        test_patient: Patient,
        test_study: Study,
    ):
        """POST /patients/{id}/file-events dispatches file flows via engine."""
        from clarinet.services.recordflow.flow_file import FlowFileRecord

        engine = RecordFlowEngine(clarinet_client)

        # Register file flow
        fr = FlowFileRecord("master_model")
        fr.on_update().invalidate_all_records("child-analysis", mode="hard")
        engine.register_flow(fr)

        # Install engine in app state
        app.state.recordflow_engine = engine

        # Create a finished child_analysis record
        await _create_record_via_client(
            clarinet_client,
            "child-analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Call file-events endpoint
        resp = await client.post(
            f"/api/patients/{test_patient.id}/file-events",
            json=["master_model"],
        )
        assert resp.status_code == 200
        assert resp.json()["dispatched"] == ["master_model"]

        # Verify child_analysis was invalidated
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child-analysis",
        )
        assert len(records) == 1
        assert records[0].status == RecordStatus.pending

        # Cleanup
        app.state.recordflow_engine = None


class TestTreeFilterContext:
    """Tree-filter (ancestors + subtree of trigger) context isolation."""

    @staticmethod
    async def _make_second_study(
        test_session: AsyncSession, patient: Patient, suffix: str = "20"
    ) -> Study:
        """Build a sibling study; ``suffix`` must be digits-only (study_uid pattern)."""
        from datetime import UTC, datetime

        s2 = Study(
            patient_id=patient.id,
            study_uid=f"1.2.3.4.5.6.7.8.9.{suffix}",
            date=datetime.now(UTC).date(),
        )
        test_session.add(s2)
        await test_session.commit()
        await test_session.refresh(s2)
        return s2

    @pytest.mark.asyncio
    async def test_study_trigger_reads_own_study_record(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        test_patient: Patient,
        test_study: Study,
    ):
        """STUDY-level trigger sees its own study's first-check, not a sibling's.

        Regression test for the previous flat ``find_records(patient_id=...)``
        + last-by-id collapse, which leaked sibling-study records into context
        and gave non-deterministic results when a patient had multiple studies.
        """
        study2 = await self._make_second_study(test_session, test_patient)

        # first-check finished on each study with different study_type.
        # Order matters: study2's first-check has the larger id (would win
        # under the old last-by-id rule).
        await _create_record_via_client(
            clarinet_client,
            "first-check",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"study_type": "CT"},
        )
        await _create_record_via_client(
            clarinet_client,
            "first-check",
            test_patient.id,
            study2.study_uid,
            status=RecordStatus.finished,
            data={"study_type": "MRI"},
        )

        # Flow: doctor-report finishes → if own first-check.study_type=="CT", create confirm-birads.
        flow = FlowRecord("doctor-report")
        flow.on_status("finished").if_(
            FlowResult("first-check", ["study_type"]) == "CT"
        ).add_record("confirm-birads")
        flow_engine.register_flow(flow)

        # Trigger on study1 — own first-check is CT → confirm-birads must be created.
        # Old code: last-by-id picks study2's MRI first-check → False → bug.
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )
        await flow_engine.handle_record_status_change(trigger)

        confirm = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="confirm-birads",
        )
        assert len(confirm) == 1, "tree-filter should pick study1's CT first-check"

        # Trigger on study2 — own first-check is MRI → no confirm-birads.
        trigger2 = await _create_record_via_client(
            clarinet_client,
            "doctor-report",
            test_patient.id,
            study2.study_uid,
            status=RecordStatus.finished,
        )
        await flow_engine.handle_record_status_change(trigger2)

        confirm2 = await clarinet_client.find_records(
            study_uid=study2.study_uid,
            record_type_name="confirm-birads",
        )
        assert len(confirm2) == 0

    @pytest.mark.asyncio
    async def test_single_strategy_skips_when_ambiguous(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        test_session: AsyncSession,
        test_patient: Patient,
        test_study: Study,
        record_types: dict[str, RecordType],
    ):
        """Default 'single' strategy on multi-record context skips the action.

        Build a PATIENT-level trigger so context contains both studies'
        first-checks. ``record('first-check').d.X`` (default single) raises
        :class:`AmbiguousContextError`. ``FlowCondition.evaluate`` re-raises
        (its own ``on_missing`` defaults to ``"raise"``); the engine's broad
        ``except Exception`` in ``_evaluate_and_run_condition`` then logs and
        suppresses the action — sentinel record stays absent.
        """
        from clarinet.models.record import RecordType

        # PATIENT-level type for the trigger and an output sentinel.
        pat_type = RecordType(
            name="pat-summary",
            level=DicomQueryLevel.PATIENT,
            unique_per_user=False,
        )
        sentinel_type = RecordType(
            name="sentinel-output",
            level=DicomQueryLevel.PATIENT,
            unique_per_user=False,
        )
        test_session.add_all([pat_type, sentinel_type])
        await test_session.commit()

        study2 = await self._make_second_study(test_session, test_patient, suffix="22")

        # Two first-checks under one patient (different studies).
        await _create_record_via_client(
            clarinet_client,
            "first-check",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"study_type": "CT"},
        )
        await _create_record_via_client(
            clarinet_client,
            "first-check",
            test_patient.id,
            study2.study_uid,
            status=RecordStatus.finished,
            data={"study_type": "MRI"},
        )

        # Flow on PATIENT-level pat-summary: single strategy → ambiguous → skip.
        flow = FlowRecord("pat-summary")
        flow.on_status("finished").if_(
            FlowResult("first-check", ["study_type"]) == "CT"
        ).add_record("sentinel-output")
        flow_engine.register_flow(flow)

        # Create + finish pat-summary (PATIENT-level, no study_uid).
        from clarinet.models import RecordCreate

        trigger_create = await clarinet_client.create_record(
            RecordCreate(record_type_name="pat-summary", patient_id=test_patient.id)
        )
        trigger = await clarinet_client.update_record_status(
            trigger_create.id, RecordStatus.finished
        )
        await flow_engine.handle_record_status_change(trigger)

        sentinels = await clarinet_client.find_records(record_type_name="sentinel-output")
        assert len(sentinels) == 0, "single-strategy ambiguity should suppress action"

    @pytest.mark.asyncio
    async def test_any_strategy_resolves_multi_record_context(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        test_session: AsyncSession,
        test_patient: Patient,
        test_study: Study,
        record_types: dict[str, RecordType],
    ):
        """``record('first-check').any().d.X`` succeeds across multi-study context."""
        from clarinet.models import RecordCreate
        from clarinet.models.record import RecordType
        from clarinet.services.recordflow import record as flow_record

        pat_type = RecordType(
            name="pat-summary-any",
            level=DicomQueryLevel.PATIENT,
            unique_per_user=False,
        )
        sentinel_type = RecordType(
            name="sentinel-any-output",
            level=DicomQueryLevel.PATIENT,
            unique_per_user=False,
        )
        test_session.add_all([pat_type, sentinel_type])
        await test_session.commit()

        study2 = await self._make_second_study(test_session, test_patient, suffix="23")

        await _create_record_via_client(
            clarinet_client,
            "first-check",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"study_type": "MRI"},
        )
        await _create_record_via_client(
            clarinet_client,
            "first-check",
            test_patient.id,
            study2.study_uid,
            status=RecordStatus.finished,
            data={"study_type": "CT"},
        )

        flow = FlowRecord("pat-summary-any")
        flow.on_status("finished").if_(
            flow_record("first-check").any().d.study_type == "CT"
        ).add_record("sentinel-any-output")
        flow_engine.register_flow(flow)

        trigger_create = await clarinet_client.create_record(
            RecordCreate(record_type_name="pat-summary-any", patient_id=test_patient.id)
        )
        trigger = await clarinet_client.update_record_status(
            trigger_create.id, RecordStatus.finished
        )
        await flow_engine.handle_record_status_change(trigger)

        sentinels = await clarinet_client.find_records(record_type_name="sentinel-any-output")
        assert len(sentinels) == 1

    @pytest.mark.asyncio
    async def test_update_record_strategy_all_updates_every_match(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        test_session: AsyncSession,
        test_patient: Patient,
        test_study: Study,
        record_types: dict[str, RecordType],
    ):
        """``update_record(strategy='all')`` updates every matching record in subtree."""
        from clarinet.models import RecordCreate
        from clarinet.models.record import RecordType

        pat_type = RecordType(
            name="pat-trigger-bulk",
            level=DicomQueryLevel.PATIENT,
            unique_per_user=False,
        )
        test_session.add(pat_type)
        await test_session.commit()

        study2 = await self._make_second_study(test_session, test_patient, suffix="24")

        # Two pending first-checks across two studies.
        for st_uid in (test_study.study_uid, study2.study_uid):
            await _create_record_via_client(
                clarinet_client,
                "first-check",
                test_patient.id,
                st_uid,
                status=RecordStatus.pending,
            )

        flow = FlowRecord("pat-trigger-bulk")
        flow.on_status("finished").update_record("first-check", status="blocked", strategy="all")
        flow_engine.register_flow(flow)

        trigger_create = await clarinet_client.create_record(
            RecordCreate(record_type_name="pat-trigger-bulk", patient_id=test_patient.id)
        )
        trigger = await clarinet_client.update_record_status(
            trigger_create.id, RecordStatus.finished
        )
        await flow_engine.handle_record_status_change(trigger)

        # Both first-checks should have been moved to 'blocked'.
        all_first_checks = await clarinet_client.find_records(record_type_name="first-check")
        assert len(all_first_checks) == 2
        assert all(r.status == RecordStatus.blocked for r in all_first_checks)

    @pytest.mark.asyncio
    async def test_update_record_strategy_single_skips_on_ambiguity(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        test_session: AsyncSession,
        test_patient: Patient,
        test_study: Study,
        record_types: dict[str, RecordType],
    ):
        """``update_record(strategy='single')`` refuses to pick when context has >1."""
        from clarinet.models import RecordCreate
        from clarinet.models.record import RecordType

        pat_type = RecordType(
            name="pat-trigger-single",
            level=DicomQueryLevel.PATIENT,
            unique_per_user=False,
        )
        test_session.add(pat_type)
        await test_session.commit()

        study2 = await self._make_second_study(test_session, test_patient, suffix="25")

        for st_uid in (test_study.study_uid, study2.study_uid):
            await _create_record_via_client(
                clarinet_client,
                "first-check",
                test_patient.id,
                st_uid,
                status=RecordStatus.pending,
            )

        flow = FlowRecord("pat-trigger-single")
        flow.on_status("finished").update_record("first-check", status="blocked")
        flow_engine.register_flow(flow)

        trigger_create = await clarinet_client.create_record(
            RecordCreate(record_type_name="pat-trigger-single", patient_id=test_patient.id)
        )
        trigger = await clarinet_client.update_record_status(
            trigger_create.id, RecordStatus.finished
        )
        await flow_engine.handle_record_status_change(trigger)

        # All first-checks remain pending — strategy='single' refused with >1 matches.
        all_first_checks = await clarinet_client.find_records(record_type_name="first-check")
        assert len(all_first_checks) == 2
        assert all(r.status == RecordStatus.pending for r in all_first_checks)

    @pytest.mark.asyncio
    async def test_series_trigger_excludes_sibling_series(
        self,
        clarinet_client: ClarinetClient,
        flow_engine: RecordFlowEngine,
        test_session: AsyncSession,
        test_patient: Patient,
        test_study: Study,
        test_series: Series,
        record_types: dict[str, RecordType],
    ):
        """SERIES trigger sees own series records only; sibling-series are out of scope."""
        from clarinet.models import RecordCreate
        from clarinet.models.record import RecordType
        from clarinet.models.study import Series

        # SERIES-level markup-meta (data carrier) + series-output (action sentinel).
        meta_type = RecordType(
            name="markup-meta",
            level=DicomQueryLevel.SERIES,
            unique_per_user=False,
        )
        output_type = RecordType(
            name="series-output",
            level=DicomQueryLevel.SERIES,
            unique_per_user=False,
        )
        test_session.add_all([meta_type, output_type])
        await test_session.commit()

        # Sibling series under the same study.
        series_b = Series(
            study_uid=test_study.study_uid,
            series_uid=f"{test_series.series_uid}.99",
            series_number=2,
            series_description="Sibling series",
        )
        test_session.add(series_b)
        await test_session.commit()
        await test_session.refresh(series_b)

        # markup-meta on each series with different kind. Order matters: series_b's
        # record gets the larger id (would win under the old last-by-id collapse).
        async def _make_meta(series_uid: str, kind: str) -> None:
            meta = await clarinet_client.create_record(
                RecordCreate(
                    record_type_name="markup-meta",
                    patient_id=test_patient.id,
                    study_uid=test_study.study_uid,
                    series_uid=series_uid,
                )
            )
            await clarinet_client.submit_record_data(meta.id, {"kind": kind})

        await _make_meta(test_series.series_uid, "manual")
        await _make_meta(series_b.series_uid, "auto")

        # Flow: series-markup finishes → if own markup-meta.kind == "manual",
        # create series-output (SERIES-level, inherits series_uid from trigger).
        flow = FlowRecord("series-markup")
        flow.on_status("finished").if_(FlowResult("markup-meta", ["kind"]) == "manual").add_record(
            "series-output"
        )
        flow_engine.register_flow(flow)

        # Trigger on series A — own markup-meta.kind == "manual" → True → output created.
        # Without tree-filter: last-by-id picks series_b's "auto" → False → no output.
        trigger_a = await clarinet_client.create_record(
            RecordCreate(
                record_type_name="series-markup",
                patient_id=test_patient.id,
                study_uid=test_study.study_uid,
                series_uid=test_series.series_uid,
            )
        )
        trigger_a = await clarinet_client.update_record_status(trigger_a.id, RecordStatus.finished)
        await flow_engine.handle_record_status_change(trigger_a)

        outputs_a = await clarinet_client.find_records(
            record_type_name="series-output", series_uid=test_series.series_uid
        )
        assert len(outputs_a) == 1, "tree-filter must isolate series A's markup-meta"

        # Trigger on series B — own markup-meta.kind == "auto" → False → no output.
        trigger_b = await clarinet_client.create_record(
            RecordCreate(
                record_type_name="series-markup",
                patient_id=test_patient.id,
                study_uid=test_study.study_uid,
                series_uid=series_b.series_uid,
            )
        )
        trigger_b = await clarinet_client.update_record_status(trigger_b.id, RecordStatus.finished)
        await flow_engine.handle_record_status_change(trigger_b)

        outputs_b = await clarinet_client.find_records(
            record_type_name="series-output", series_uid=series_b.series_uid
        )
        assert len(outputs_b) == 0
