"""Integration tests for RecordFlow engine with real DB and API.

Uses clarinet_client fixture (real HTTP client → FastAPI app → in-memory SQLite).
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.app import app
from src.client import ClarinetClient
from src.models.base import DicomQueryLevel, RecordStatus
from src.models.patient import Patient
from src.models.record import RecordCreate, RecordRead, RecordType
from src.models.study import Study
from src.services.recordflow import FlowRecord, FlowResult, RecordFlowEngine
from src.services.recordflow.flow_record import ENTITY_REGISTRY, RECORD_REGISTRY


@pytest.fixture(autouse=True)
def _clear_registry():
    """Clear the global FlowRecord registries between tests."""
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()
    yield
    RECORD_REGISTRY.clear()
    ENTITY_REGISTRY.clear()


@pytest_asyncio.fixture(autouse=True)
async def _auth_override(test_session: AsyncSession):
    """Bypass auth for recordflow integration tests."""
    from uuid import uuid4

    from src.api.app import app
    from src.api.auth_config import current_active_user, current_superuser
    from src.models.user import User
    from src.utils.auth import get_password_hash

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
        "doctor_report",
        "ai_analysis",
        "expert_check",
        "confirm_birads",
        "parent_model",
        "child_analysis",
    ]
    series_level_types = ["series_markup"]
    for name in study_level_types:
        rt = RecordType(name=name, level=DicomQueryLevel.STUDY)
        test_session.add(rt)
        types[name] = rt
    for name in series_level_types:
        rt = RecordType(name=name, level=DicomQueryLevel.SERIES)
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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished").add_record("ai_analysis")
        flow_engine.register_flow(flow)

        # Create trigger record
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Execute flow
        await flow_engine.handle_record_status_change(trigger)

        # Verify: ai_analysis record was created
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai_analysis",
        )
        assert len(records) == 1
        assert records[0].record_type.name == "ai_analysis"

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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor_report", ["confidence"]) < 70).add_record("expert_check")
        flow_engine.register_flow(flow)

        # Create trigger record with confidence=50 (< 70 → True)
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"confidence": 50},
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="expert_check",
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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor_report", ["confidence"]) < 70).add_record("expert_check")
        flow_engine.register_flow(flow)

        # confidence=90 → condition False
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"confidence": 90},
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="expert_check",
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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor_report", ["confidence"]) < 70).add_record("expert_check")
        flow.else_().add_record("ai_analysis")
        flow_engine.register_flow(flow)

        # confidence=90 → if_ False → else_ executes
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"confidence": 90},
        )

        await flow_engine.handle_record_status_change(trigger)

        # expert_check should NOT exist
        expert = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="expert_check",
        )
        assert len(expert) == 0

        # ai_analysis SHOULD exist (else branch)
        ai = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai_analysis",
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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished")
        flow.if_(
            FlowResult("doctor_report", ["diagnosis"]) != FlowResult("ai_analysis", ["diagnosis"])
        ).add_record("confirm_birads")
        flow_engine.register_flow(flow)

        # Create ai_analysis first (context record)
        await _create_record_via_client(
            clarinet_client,
            "ai_analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"diagnosis": "benign"},
        )

        # Create doctor_report with different diagnosis
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"diagnosis": "malignant"},
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="confirm_birads",
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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished").update_record("ai_analysis", status="finished")
        flow_engine.register_flow(flow)

        # Create ai_analysis (pending)
        await _create_record_via_client(
            clarinet_client,
            "ai_analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.pending,
        )

        # Create trigger
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        await flow_engine.handle_record_status_change(trigger)

        # Verify ai_analysis status changed
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai_analysis",
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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished").add_record("ai_analysis")
        flow_engine.register_flow(flow)

        # Create record with status=pending (not "finished")
        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.pending,
        )

        await flow_engine.handle_record_status_change(trigger)

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai_analysis",
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

        flow = FlowRecord("doctor_report")
        flow.on_status("finished").call(custom_handler)
        flow_engine.register_flow(flow)

        trigger = await _create_record_via_client(
            clarinet_client,
            "doctor_report",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        await flow_engine.handle_record_status_change(trigger)

        assert len(call_log) == 1
        assert call_log[0]["record_type"] == "doctor_report"
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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished").add_record("ai_analysis")
        app_with_engine.register_flow(flow)

        # Create a record via API (status=pending by default)
        create_resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor_report",
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
            record_type_name="ai_analysis",
        )
        assert len(records) == 1
        assert records[0].record_type.name == "ai_analysis"

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
        flow = FlowRecord("doctor_report")
        flow.on_status("finished")
        flow.if_(FlowResult("doctor_report", ["confidence"]) < 70).add_record("expert_check")
        app_with_engine.register_flow(flow)

        # Create record
        create_resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "doctor_report",
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
            record_type_name="expert_check",
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
                "record_type_name": "doctor_report",
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
            record_type_name="ai_analysis",
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
        flow = FlowRecord("parent_model")
        flow.on_data_update().invalidate_records("child_analysis", mode="hard")
        flow_engine.register_flow(flow)

        # Create parent record (finished with data)
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent_model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"model_version": "v1"},
        )

        # Create child record (finished)
        await _create_record_via_client(
            clarinet_client,
            "child_analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update flow
        await flow_engine.handle_record_data_update(parent_record)

        # Verify child is now pending and has invalidation info
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child_analysis",
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
        flow = FlowRecord("parent_model")
        flow.on_data_update().invalidate_records("child_analysis", mode="soft")
        flow_engine.register_flow(flow)

        # Create parent record (finished with data)
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent_model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"model_version": "v1"},
        )

        # Create child record (finished)
        await _create_record_via_client(
            clarinet_client,
            "child_analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update flow
        await flow_engine.handle_record_data_update(parent_record)

        # Verify child status unchanged but context_info updated
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child_analysis",
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
        flow = FlowRecord("parent_model")
        flow.on_data_update().invalidate_records("parent_model", mode="hard")
        flow_engine.register_flow(flow)

        # Create two parent_model records
        first_record = await _create_record_via_client(
            clarinet_client,
            "parent_model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"version": "1"},
        )

        second_record = await _create_record_via_client(
            clarinet_client,
            "parent_model",
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
            record_type_name="parent_model",
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
        flow = FlowRecord("parent_model")
        flow.on_data_update().invalidate_records(
            "child_analysis", mode="hard", callback=invalidation_callback
        )
        flow_engine.register_flow(flow)

        # Create parent and child
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent_model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"data": "test"},
        )

        await _create_record_via_client(
            clarinet_client,
            "child_analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update
        await flow_engine.handle_record_data_update(parent_record)

        # Verify callback was called with the target (child) record
        assert len(call_log) == 1
        assert call_log[0]["record_type"] == "child_analysis"
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
        flow = FlowRecord("parent_model")
        flow.on_data_update().invalidate_records("child_analysis", "ai_analysis", mode="hard")
        flow_engine.register_flow(flow)

        # Create parent
        parent_record = await _create_record_via_client(
            clarinet_client,
            "parent_model",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
            data={"data": "test"},
        )

        # Create child_analysis (finished)
        await _create_record_via_client(
            clarinet_client,
            "child_analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Create ai_analysis (finished)
        await _create_record_via_client(
            clarinet_client,
            "ai_analysis",
            test_patient.id,
            test_study.study_uid,
            status=RecordStatus.finished,
        )

        # Trigger data update
        await flow_engine.handle_record_data_update(parent_record)

        # Verify both child_analysis and ai_analysis are invalidated
        child_records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="child_analysis",
        )
        assert len(child_records) == 1
        assert child_records[0].status == RecordStatus.pending

        ai_records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="ai_analysis",
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
        flow = FlowRecord("parent_model")
        flow.on_data_update().invalidate_records("child_analysis", mode="hard")
        app_with_engine.register_flow(flow)

        # Create parent_model via API
        create_resp = await client.post(
            "/api/records/",
            json={
                "record_type_name": "parent_model",
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
                "record_type_name": "child_analysis",
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
            record_type_name="child_analysis",
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
            "parent_model",
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
            "parent_model",
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
    ):
        """Engine entity flow creates a record when triggered for a series."""
        engine = RecordFlowEngine(clarinet_client)

        fr = FlowRecord("series", entity_trigger="series")
        fr.add_record("series_markup")
        engine.register_flow(fr)

        await engine.handle_entity_created(
            "series",
            patient_id=test_patient.id,
            study_uid=test_study.study_uid,
            series_uid="1.2.3.4.5.6.7.8.9.10",
        )

        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="series_markup",
        )
        assert len(records) == 1
        assert records[0].record_type.name == "series_markup"
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
        fr.add_record("series_markup")
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

        # Verify: series_markup was created by the entity flow
        records = await clarinet_client.find_records(
            study_uid=test_study.study_uid,
            record_type_name="series_markup",
        )
        assert len(records) == 1
        assert records[0].series_uid == "9.8.7.6.5.4.3.2.1"
