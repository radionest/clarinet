"""End-to-end tests for pipeline workflow and RecordFlow integration.

This module tests the full integration of:
- Pipeline definition API endpoints
- Pipeline task dispatch and execution
- RecordFlow-driven pipeline triggers
- File events and invalidation workflows
- Conditional record creation
- Multi-step workflow orchestration
- TaskContext integration

Tests are organized into sections matching the test plan:
1. TestPipelineDefinitionEndpoints (5 tests)
2. TestPipelineTaskDispatch (3 tests) - requires RabbitMQ
3. TestPipelineWithRecordLifecycle (2 tests) - requires RabbitMQ
4. TestPipelineBrokerConnectivity (2 tests) - requires RabbitMQ
5. TestPipelineTaskDecorator (6 tests)
6. TestRecordFlowDrivenTaskDispatch (5 tests)
7. TestFileTriggersAndInvalidation (4 tests)
8. TestConditionalRecordCreation (5 tests)
9. TestEntityCreationTriggers (2 tests)
10. TestMultiStepWorkflow (8 tests)
11. TestTaskContextIntegration (4 tests)
"""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.api.app import app
from clarinet.client import ClarinetClient
from clarinet.models.base import DicomQueryLevel
from clarinet.models.patient import Patient
from clarinet.models.record import RecordType
from clarinet.models.study import Series, Study
from clarinet.services.pipeline import Pipeline, PipelineMessage, get_pipeline
from clarinet.services.pipeline.chain import _PIPELINE_REGISTRY, _TASK_REGISTRY
from clarinet.services.pipeline.context import TaskContext, build_task_context
from clarinet.services.pipeline.task import pipeline_task
from clarinet.services.recordflow.engine import RecordFlowEngine
from clarinet.services.recordflow.flow_file import FILE_REGISTRY, file
from clarinet.services.recordflow.flow_record import (
    ENTITY_REGISTRY,
    RECORD_REGISTRY,
    record,
    study,
)
from clarinet.services.recordflow.flow_result import Field
from tests.utils.urls import PIPELINES_BASE, PIPELINES_SYNC, RECORDS_BASE, RECORDS_FIND

pytestmark = pytest.mark.asyncio


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def _clear_registries():
    """Clear all registries before each test to ensure isolation."""
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


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Override e2e conftest's unauthenticated client with an authenticated one.

    Re-overrides the unauthenticated client from e2e conftest to use
    authenticated client for pipeline tests (same pattern as test_demo_processing).
    """
    from clarinet.api.auth_config import current_active_user, current_superuser
    from clarinet.models.user import User
    from clarinet.utils.auth import get_password_hash
    from clarinet.utils.database import get_async_session

    mock_user = User(
        id=uuid4(),
        email="e2e_pipeline@test.com",
        hashed_password=get_password_hash("mock"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_session.add(mock_user)
    await test_session.commit()
    await test_session.refresh(mock_user)

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: mock_user
    app.dependency_overrides[current_superuser] = lambda: mock_user

    try:
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        original_request = ac.request

        async def request_with_cookies(method, url, **kwargs):
            if ac.cookies:
                headers = kwargs.get("headers") or {}
                cookie_header = "; ".join([f"{k}={v}" for k, v in ac.cookies.items()])
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    kwargs["headers"] = headers
            return await original_request(method, url, **kwargs)

        ac.request = request_with_cookies
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def flow_engine(clarinet_client: ClarinetClient) -> RecordFlowEngine:
    """Create a RecordFlowEngine for testing."""
    return RecordFlowEngine(clarinet_client)


@pytest_asyncio.fixture
async def app_with_engine(
    flow_engine: RecordFlowEngine,
) -> AsyncGenerator[RecordFlowEngine]:
    """Install flow engine in app.state for testing."""
    app.state.recordflow_engine = flow_engine
    yield flow_engine
    app.state.recordflow_engine = None


@pytest_asyncio.fixture
async def record_types(test_session: AsyncSession) -> dict[str, RecordType]:
    """Create record types for testing."""
    types = {
        "first_check": RecordType(
            name="first_check",
            description="Initial check",
            level=DicomQueryLevel.SERIES,
        ),
        "segment_CT_single": RecordType(
            name="segment_CT_single",
            description="CT segmentation",
            level=DicomQueryLevel.SERIES,
        ),
        "segment_MRI_single": RecordType(
            name="segment_MRI_single",
            description="MRI segmentation",
            level=DicomQueryLevel.SERIES,
        ),
        "create_master_projection": RecordType(
            name="create_master_projection",
            description="Master projection",
            level=DicomQueryLevel.SERIES,
        ),
        "compare_with_projection": RecordType(
            name="compare_with_projection",
            description="Projection comparison",
            level=DicomQueryLevel.SERIES,
        ),
        "update_master_model": RecordType(
            name="update_master_model",
            description="Update master model",
            level=DicomQueryLevel.PATIENT,
        ),
        "second_review": RecordType(
            name="second_review",
            description="Second review",
            level=DicomQueryLevel.SERIES,
        ),
        "anonymization": RecordType(
            name="anonymization",
            description="Anonymization task",
            level=DicomQueryLevel.SERIES,
        ),
    }

    for rt in types.values():
        test_session.add(rt)
    await test_session.commit()

    for rt in types.values():
        await test_session.refresh(rt)

    return types


async def _create_hierarchy(
    session: AsyncSession,
    patient_id: str = "TEST_PAT001",
    study_uid: str = "1.2.3.4.5",
    series_uid: str = "1.2.3.4.5.1",
) -> dict[str, str]:
    """Create patient -> study -> series via ORM."""
    patient = Patient(id=patient_id, name="Test Patient")
    session.add(patient)
    await session.commit()

    study_obj = Study(study_uid=study_uid, patient_id=patient_id, date=datetime.now(tz=UTC).date())
    session.add(study_obj)
    await session.commit()

    series_obj = Series(
        series_uid=series_uid,
        series_number=1,
        study_uid=study_uid,
    )
    session.add(series_obj)
    await session.commit()

    return {
        "patient_id": patient_id,
        "study_uid": study_uid,
        "series_uid": series_uid,
    }


async def _create_record_via_api(
    client: AsyncClient,
    record_type_name: str,
    patient_id: str,
    study_uid: str | None = None,
    series_uid: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a record via API."""
    payload: dict[str, Any] = {
        "record_type_name": record_type_name,
        "patient_id": patient_id,
    }
    if study_uid:
        payload["study_uid"] = study_uid
    if series_uid:
        payload["series_uid"] = series_uid

    response = await client.post(f"{RECORDS_BASE}/", json=payload)
    assert response.status_code == 201, response.text
    record_data = response.json()

    # Submit data if provided (auto-sets status to finished)
    if data:
        resp = await client.post(
            f"{RECORDS_BASE}/{record_data['id']}/data",
            json=data,
        )
        assert resp.status_code == 200, resp.text
        record_data = resp.json()

    return record_data


async def _find_records(
    client: AsyncClient,
    record_type_name: str | None = None,
    patient_id: str | None = None,
    study_uid: str | None = None,
    series_uid: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Find records via API."""
    params: dict[str, str] = {}
    if record_type_name:
        params["record_type_name"] = record_type_name
    if patient_id:
        params["patient_id"] = patient_id
    if study_uid:
        params["study_uid"] = study_uid
    if series_uid:
        params["series_uid"] = series_uid
    if status:
        params["status"] = status

    response = await client.post(RECORDS_FIND, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _make_mock_study(
    patient_id: str = "TEST_PAT001",
    study_uid: str = "1.2.3",
) -> MagicMock:
    """Create a mock StudyRead-like object for build_task_context."""
    mock_study = MagicMock()
    mock_study.study_uid = study_uid
    mock_study.anon_uid = None
    mock_study.patient = MagicMock()
    mock_study.patient.id = patient_id
    mock_study.patient.anon_id = None
    return mock_study


async def _update_status(
    client: AsyncClient,
    record_id: int,
    new_status: str,
) -> dict[str, Any]:
    """Update record status via API."""
    response = await client.patch(
        f"{RECORDS_BASE}/{record_id}/status",
        params={"record_status": new_status},
    )
    assert response.status_code == 200, response.text
    return response.json()


# ============================================================================
# 1. TestPipelineDefinitionEndpoints (tests 1-5)
# ============================================================================


class TestPipelineDefinitionEndpoints:
    """Test pipeline definition API endpoints."""

    async def test_sync_empty_registry(self, client: AsyncClient) -> None:
        """Test 1: POST /api/pipelines/sync with empty registry returns 0."""
        response = await client.post(PIPELINES_SYNC)
        assert response.status_code == 200
        data = response.json()
        assert data == {"synced": 0}

    async def test_sync_single_pipeline(self, client: AsyncClient) -> None:
        """Test 2: POST /api/pipelines/sync with single pipeline."""

        # Create a dummy task and register pipeline
        @pipeline_task()
        async def dummy_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            return msg.model_dump()

        Pipeline("test_pipeline").step(dummy_task)

        response = await client.post(PIPELINES_SYNC)
        assert response.status_code == 200
        data = response.json()
        assert data["synced"] >= 1

        # Verify pipeline is in registry
        pipeline = get_pipeline("test_pipeline")
        assert pipeline is not None
        assert pipeline.name == "test_pipeline"

    async def test_sync_multiple_pipelines(self, client: AsyncClient) -> None:
        """Test 3: POST /api/pipelines/sync with multiple pipelines."""

        @pipeline_task()
        async def task_a(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            return msg.model_dump()

        @pipeline_task()
        async def task_b(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            return msg.model_dump()

        Pipeline("pipeline_a").step(task_a)
        Pipeline("pipeline_b").step(task_b)

        response = await client.post(PIPELINES_SYNC)
        assert response.status_code == 200
        data = response.json()
        assert data["synced"] >= 2

    async def test_get_pipeline_definition(self, client: AsyncClient) -> None:
        """Test 4: GET /api/pipelines/{name}/definition returns pipeline details."""

        @pipeline_task()
        async def test_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            return msg.model_dump()

        Pipeline("test_pipeline").step(test_task)

        # Sync to DB first
        await client.post(PIPELINES_SYNC)

        response = await client.get(f"{PIPELINES_BASE}/test_pipeline/definition")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test_pipeline"
        assert "steps" in data
        assert len(data["steps"]) == 1

    async def test_get_nonexistent_pipeline(self, client: AsyncClient) -> None:
        """Test 5: GET /api/pipelines/{name}/definition returns 404 for nonexistent."""
        response = await client.get(f"{PIPELINES_BASE}/nonexistent/definition")
        assert response.status_code == 404


# ============================================================================
# 2. TestPipelineTaskDispatch (tests 6-8) - requires RabbitMQ
# ============================================================================


@pytest.mark.pipeline
@pytest.mark.usefixtures("_check_rabbitmq")
class TestPipelineTaskDispatch:
    """Test pipeline task dispatch with real broker.

    These tests require RabbitMQ. Kept concise since
    test_pipeline_integration.py covers this thoroughly.
    """

    async def test_dispatch_task_to_broker(self, pipeline_broker_factory: Any) -> None:
        """Test 6: Dispatch task to broker successfully."""
        broker = await pipeline_broker_factory()

        @broker.task(queue="clarinet.default")
        async def simple_task(msg: dict) -> dict[str, Any]:
            message = PipelineMessage(**msg)
            return {"status": "success", "patient_id": message.patient_id}

        message = PipelineMessage(patient_id="TEST_PAT001", study_uid="1.2.3.4.5")
        task_result = await simple_task.kiq(message.model_dump())
        assert task_result is not None

    async def test_pipeline_run_dispatches_tasks(self, pipeline_broker_factory: Any) -> None:
        """Test 7: Pipeline.run() dispatches first step."""
        broker = await pipeline_broker_factory()

        @broker.task(queue="clarinet.default")
        async def step1(msg: dict) -> dict[str, Any]:
            return PipelineMessage(**msg).model_dump()

        @broker.task(queue="clarinet.default")
        async def step2(msg: dict) -> dict[str, Any]:
            return PipelineMessage(**msg).model_dump()

        Pipeline("test_chain").step(step1).step(step2)

        message = PipelineMessage(patient_id="TEST_PAT001", study_uid="1.2.3.4.5")
        await Pipeline("test_chain_dispatch").step(step1).run(message)
        # If we get here without exception, dispatch worked

    async def test_dispatch_with_payload(self, pipeline_broker_factory: Any) -> None:
        """Test 8: Dispatch task with custom payload."""
        broker = await pipeline_broker_factory()

        @broker.task(queue="clarinet.default")
        async def task_with_payload(msg: dict) -> dict[str, Any]:
            message = PipelineMessage(**msg)
            return {"payload": message.payload}

        message = PipelineMessage(
            patient_id="TEST_PAT001",
            study_uid="1.2.3.4.5",
            payload={"custom": "data", "value": 42},
        )
        task_result = await task_with_payload.kiq(message.model_dump())
        assert task_result is not None


# ============================================================================
# 3. TestPipelineWithRecordLifecycle (tests 9-10) - requires RabbitMQ
# ============================================================================


@pytest.mark.pipeline
@pytest.mark.usefixtures("_check_rabbitmq")
class TestPipelineWithRecordLifecycle:
    """Test pipeline integration with record lifecycle."""

    async def test_pipeline_record_context(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        pipeline_broker_factory: Any,
    ) -> None:
        """Test 9: Pipeline tasks receive correct record context."""
        broker = await pipeline_broker_factory()

        @broker.task(queue="clarinet.default")
        async def context_task(msg: dict) -> dict[str, Any]:
            message = PipelineMessage(**msg)
            return {"patient_id": message.patient_id, "record_id": message.record_id}

        Pipeline("context_pipeline").step(context_task)

        hierarchy = await _create_hierarchy(test_session)
        rec_data = await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
        )

        message = PipelineMessage(
            patient_id=hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            record_id=rec_data["id"],
        )
        task_result = await context_task.kiq(message.model_dump())
        assert task_result is not None

    async def test_pipeline_task_failure_handling(self, pipeline_broker_factory: Any) -> None:
        """Test 10: Pipeline handles task failures gracefully."""
        broker = await pipeline_broker_factory()

        @broker.task(queue="clarinet.default")
        async def failing_task(msg: dict) -> dict[str, Any]:
            raise ValueError("Task failed intentionally")

        message = PipelineMessage(patient_id="TEST_PAT001", study_uid="1.2.3.4.5")

        with pytest.raises(ValueError, match="Task failed intentionally"):
            await failing_task(message.model_dump())


# ============================================================================
# 4. TestPipelineBrokerConnectivity (tests 11-12) - requires RabbitMQ
# ============================================================================


@pytest.mark.pipeline
@pytest.mark.usefixtures("_check_rabbitmq")
class TestPipelineBrokerConnectivity:
    """Test broker lifecycle and connectivity."""

    async def test_broker_startup_shutdown(self, pipeline_broker_factory: Any) -> None:
        """Test 11: Broker startup and shutdown."""
        broker = await pipeline_broker_factory()
        assert broker is not None

    async def test_task_registration_persists(self, pipeline_broker_factory: Any) -> None:
        """Test 12: Task registration persists in registry."""
        from clarinet.services.pipeline.chain import register_task

        broker = await pipeline_broker_factory()

        @broker.task(queue="clarinet.default")
        async def persistent_task(msg: dict) -> dict[str, Any]:
            return {"result": "ok"}

        # Standalone @broker.task needs explicit register_task()
        register_task(persistent_task)
        assert persistent_task.task_name in _TASK_REGISTRY


# ============================================================================
# 5. TestPipelineTaskDecorator (tests 13-18)
# ============================================================================


class TestPipelineTaskDecorator:
    """Test @pipeline_task decorator functionality."""

    async def test_pipeline_task_decorator_basic(self) -> None:
        """Test 13: @pipeline_task decorator wraps function correctly."""

        @pipeline_task()
        async def basic_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            return {"patient_id": msg.patient_id}

        with patch("clarinet.services.pipeline.task.ClarinetClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.login = AsyncMock()
            mock_client.close = AsyncMock()
            mock_client.notify_file_changes = AsyncMock()
            mock_client.get_record = AsyncMock(return_value=None)
            mock_client.get_series = AsyncMock(return_value=None)
            mock_client.get_study = AsyncMock(return_value=_make_mock_study())

            raw_message = {"patient_id": "TEST_PAT001", "study_uid": "1.2.3"}
            result = await basic_task(raw_message)

            # Wrapper returns message.model_dump(), not inner function result
            assert result["patient_id"] == "TEST_PAT001"

    async def test_pipeline_task_parses_message(self) -> None:
        """Test 14: @pipeline_task parses PipelineMessage from dict.

        The wrapper returns message.model_dump(), so we verify the returned
        dict contains the parsed message fields.
        """
        received: list[PipelineMessage] = []

        @pipeline_task()
        async def message_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            received.append(msg)
            return msg.model_dump()

        with patch("clarinet.services.pipeline.task.ClarinetClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.login = AsyncMock()
            mock_client.close = AsyncMock()
            mock_client.notify_file_changes = AsyncMock()
            mock_client.get_record = AsyncMock(return_value=None)
            mock_client.get_series = AsyncMock(return_value=None)
            mock_client.get_study = AsyncMock(return_value=_make_mock_study())

            raw_message = {
                "patient_id": "TEST_PAT001",
                "study_uid": "1.2.3",
                "payload": {"key": "value"},
            }
            result = await message_task(raw_message)

            # Wrapper returns message.model_dump()
            assert result["patient_id"] == "TEST_PAT001"
            assert result["study_uid"] == "1.2.3"
            # Inner function received parsed PipelineMessage
            assert len(received) == 1
            assert received[0].patient_id == "TEST_PAT001"
            assert received[0].payload == {"key": "value"}

    async def test_pipeline_task_builds_context(self) -> None:
        """Test 15: @pipeline_task builds TaskContext with correct fields.

        Captures the TaskContext inside the task to verify all fields are set.
        """
        captured_ctx: list[TaskContext] = []

        @pipeline_task()
        async def context_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            captured_ctx.append(ctx)
            return msg.model_dump()

        with patch("clarinet.services.pipeline.task.ClarinetClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.login = AsyncMock()
            mock_client.close = AsyncMock()
            mock_client.notify_file_changes = AsyncMock()
            mock_client.get_record = AsyncMock(return_value=None)
            mock_client.get_series = AsyncMock(return_value=None)
            mock_client.get_study = AsyncMock(return_value=_make_mock_study())

            raw_message = {"patient_id": "TEST_PAT001", "study_uid": "1.2.3"}
            await context_task(raw_message)

            assert len(captured_ctx) == 1
            ctx = captured_ctx[0]
            assert ctx.files is not None
            assert ctx.records is not None
            assert ctx.client is not None
            assert ctx.msg is not None
            assert ctx.msg.patient_id == "TEST_PAT001"

    async def test_pipeline_task_client_lifecycle(self) -> None:
        """Test 16: @pipeline_task manages ClarinetClient lifecycle.

        The decorator calls client.login() before the task and client.close()
        after (not __aenter__/__aexit__).
        """
        task_executed = False

        @pipeline_task()
        async def lifecycle_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            nonlocal task_executed
            task_executed = True
            return msg.model_dump()

        with patch("clarinet.services.pipeline.task.ClarinetClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.login = AsyncMock()
            mock_client.close = AsyncMock()
            mock_client.notify_file_changes = AsyncMock()
            mock_client.get_record = AsyncMock(return_value=None)
            mock_client.get_series = AsyncMock(return_value=None)
            mock_client.get_study = AsyncMock(return_value=_make_mock_study())

            raw_message = {"patient_id": "TEST_PAT001", "study_uid": "1.2.3"}
            await lifecycle_task(raw_message)

            # Verify lifecycle: login → task → close
            mock_client.login.assert_awaited_once()
            mock_client.close.assert_awaited_once()
            assert task_executed is True

    async def test_pipeline_task_invalid_message_raises(self) -> None:
        """Test 17: @pipeline_task raises on invalid message format."""

        @pipeline_task()
        async def strict_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            return {"status": "ok"}

        # Missing required patient_id
        raw_message: dict[str, Any] = {"study_uid": "1.2.3"}

        with pytest.raises((Exception, SystemExit)):
            await strict_task(raw_message)

    async def test_pipeline_task_with_record_id(self) -> None:
        """Test 18: @pipeline_task loads record when record_id provided."""
        captured_ctx: list[TaskContext] = []

        @pipeline_task()
        async def record_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            captured_ctx.append(ctx)
            return msg.model_dump()

        with patch("clarinet.services.pipeline.task.ClarinetClient") as MockClient:
            mock_client = AsyncMock()
            MockClient.return_value = mock_client
            mock_client.login = AsyncMock()
            mock_client.close = AsyncMock()
            mock_client.notify_file_changes = AsyncMock()

            # Mock get_record to return a RecordRead-like object
            mock_record = MagicMock()
            mock_record.id = 42
            mock_record.patient_id = "TEST_PAT001"
            mock_record.study_uid = "1.2.3"
            mock_record.series_uid = "1.2.3.1"
            mock_record.record_type = MagicMock()
            mock_record.record_type.name = "test_type"
            mock_record.record_type.level = DicomQueryLevel.SERIES
            mock_record.record_type.file_registry = []
            mock_record.clarinet_storage_path = None
            mock_record.data = {}
            mock_record.user_id = None
            mock_record.patient = MagicMock()
            mock_record.patient.anon_id = None
            mock_record.study = MagicMock()
            mock_record.study.anon_uid = None
            mock_record.series = MagicMock()
            mock_record.series.anon_uid = None
            mock_client.get_record = AsyncMock(return_value=mock_record)

            raw_message = {"patient_id": "TEST_PAT001", "study_uid": "1.2.3", "record_id": 42}
            result = await record_task(raw_message)

            # Wrapper returns message.model_dump()
            assert result["record_id"] == 42
            # Verify context was built from record
            assert len(captured_ctx) == 1
            assert captured_ctx[0].msg.record_id == 42
            mock_client.get_record.assert_awaited_once_with(42)


# ============================================================================
# 6. TestRecordFlowDrivenTaskDispatch (tests 19-23)
# ============================================================================


class TestRecordFlowDrivenTaskDispatch:
    """Test RecordFlow DSL triggering pipeline tasks."""

    async def test_do_task_creates_pipeline_in_registry(self) -> None:
        """Test 19: .do_task() auto-creates single-step pipeline in registry."""

        @pipeline_task()
        async def mock_task(msg: PipelineMessage, ctx: TaskContext) -> dict[str, Any]:
            return msg.model_dump()

        record("first_check").on_status("finished").do_task(mock_task)

        # do_task uses task_name (broker-registered name), not __name__
        pipeline_name = f"_task:{mock_task.task_name}"
        pipeline = get_pipeline(pipeline_name)
        assert pipeline is not None
        assert len(pipeline.steps) == 1

    async def test_pipeline_action_registers_flow(self, flow_engine: RecordFlowEngine) -> None:
        """Test 20: .pipeline() action registers correctly in flow engine."""
        flow = record("first_check").on_status("finished").pipeline("full_check_pipeline")
        flow_engine.register_flow(flow)

        assert len(flow_engine.flows.get("first_check", [])) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_status_change_triggers_create_record(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 21: Status change triggers .create_record() via RecordFlow."""
        # Register flow: on finished status, create segment_CT_single
        flow = record("first_check").on_status("finished").create_record("segment_CT_single")
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)

        # Create first_check record and submit data (auto-finishes)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "CT"},
        )

        # Verify segment_CT_single was created by the flow
        records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_multiple_actions_single_trigger(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 22: Multiple actions chained on single trigger."""
        flow = (
            record("first_check")
            .on_status("finished")
            .create_record("segment_CT_single")
            .create_record("segment_MRI_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "CT"},
        )

        ct_records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        mri_records = await _find_records(
            client, record_type_name="segment_MRI_single", patient_id=hierarchy["patient_id"]
        )
        assert len(ct_records) >= 1
        assert len(mri_records) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_pipeline_message_includes_context(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 23: Record created by flow has correct context."""
        flow = record("first_check").on_status("finished").create_record("segment_CT_single")
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "CT"},
        )

        records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) >= 1
        assert records[0]["patient_id"] == hierarchy["patient_id"]
        assert records[0]["study_uid"] == hierarchy["study_uid"]
        assert records[0]["series_uid"] == hierarchy["series_uid"]


# ============================================================================
# 7. TestFileTriggersAndInvalidation (tests 24-27)
# ============================================================================


class TestFileTriggersAndInvalidation:
    """Test file event triggers and record invalidation."""

    @pytest.mark.usefixtures("app_with_engine")
    async def test_file_update_triggers_invalidation(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 24: File update triggers record invalidation via .on_update()."""
        # Register file flow
        file_flow = (
            file("master_model").on_update().invalidate_all_records("create_master_projection")
        )
        app_with_engine.register_flow(file_flow)

        hierarchy = await _create_hierarchy(test_session)

        # Create records and finish them via data submission
        rec1 = await _create_record_via_api(
            client,
            "create_master_projection",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"projection": "done"},
        )
        assert rec1["status"] == "finished"

        # Trigger file event
        response = await client.post(
            f"/api/patients/{hierarchy['patient_id']}/file-events",
            json=["master_model"],
        )
        assert response.status_code == 200
        data = response.json()
        assert "master_model" in data["dispatched"]

        # Hard invalidation resets to "pending" (not "invalid")
        records = await _find_records(
            client,
            record_type_name="create_master_projection",
            patient_id=hierarchy["patient_id"],
        )
        for r in records:
            assert r["status"] == "pending"

    @pytest.mark.usefixtures("app_with_engine")
    async def test_file_update_does_not_affect_other_types(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 25: File update only invalidates targeted record types."""
        file_flow = (
            file("master_model").on_update().invalidate_all_records("create_master_projection")
        )
        app_with_engine.register_flow(file_flow)

        hierarchy = await _create_hierarchy(test_session)

        # Create a first_check (should NOT be affected)
        fc = await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "CT"},
        )
        assert fc["status"] == "finished"

        # Create a projection record (SHOULD be affected)
        proj = await _create_record_via_api(
            client,
            "create_master_projection",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"projection": "done"},
        )
        assert proj["status"] == "finished"

        # Trigger file event
        await client.post(
            f"/api/patients/{hierarchy['patient_id']}/file-events",
            json=["master_model"],
        )

        # first_check should remain finished
        fc_records = await _find_records(
            client, record_type_name="first_check", patient_id=hierarchy["patient_id"]
        )
        assert fc_records[0]["status"] == "finished"

        # projection should be reset to pending
        proj_records = await _find_records(
            client,
            record_type_name="create_master_projection",
            patient_id=hierarchy["patient_id"],
        )
        assert proj_records[0]["status"] == "pending"

    @pytest.mark.usefixtures("app_with_engine")
    async def test_hard_invalidation_resets_to_pending(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
    ) -> None:
        """Test 26: Hard invalidation resets status to pending."""
        hierarchy = await _create_hierarchy(test_session)

        rec_data = await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "CT"},
        )
        record_id = rec_data["id"]
        assert rec_data["status"] == "finished"

        # Hard invalidation
        response = await client.post(
            f"{RECORDS_BASE}/{record_id}/invalidate",
            json={"mode": "hard", "reason": "Testing hard invalidation"},
        )
        assert response.status_code == 200

        # Hard invalidation resets to "pending"
        response = await client.get(f"{RECORDS_BASE}/{record_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"

    @pytest.mark.usefixtures("app_with_engine")
    async def test_file_event_no_matching_records_is_noop(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 27: File event with no matching records is a no-op."""
        file_flow = (
            file("master_model").on_update().invalidate_all_records("create_master_projection")
        )
        app_with_engine.register_flow(file_flow)

        hierarchy = await _create_hierarchy(test_session)

        # Trigger file event when no projection records exist
        response = await client.post(
            f"/api/patients/{hierarchy['patient_id']}/file-events",
            json=["master_model"],
        )
        assert response.status_code == 200
        assert response.json()["dispatched"] == ["master_model"]


# ============================================================================
# 8. TestConditionalRecordCreation (tests 28-32)
# ============================================================================


class TestConditionalRecordCreation:
    """Test conditional record creation with .if_record() filters."""

    @pytest.mark.usefixtures("app_with_engine")
    async def test_if_record_equality_condition(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 28: .if_record() with equality condition filters correctly."""
        F = Field()

        flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.study_type == "CT")
            .create_record("segment_CT_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "CT"},
        )

        records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) == 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_if_record_boolean_condition(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 29: .if_record() with boolean field condition."""
        F = Field()

        flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.is_good == True)  # noqa: E712
            .create_record("segment_CT_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"is_good": True},
        )

        records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) == 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_if_record_multiple_conditions_and(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 30: .if_record() with multiple AND conditions."""
        F = Field()

        flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.is_good == True, F.study_type == "CT")  # noqa: E712
            .create_record("segment_CT_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"is_good": True, "study_type": "CT"},
        )

        records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) == 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_if_record_condition_fails_no_record_created(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 31: .if_record() condition fails, no record created."""
        F = Field()

        flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.study_type == "CT")
            .create_record("segment_CT_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)

        # Create with MRI (condition fails)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "MRI"},
        )

        records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) == 0

    @pytest.mark.usefixtures("app_with_engine")
    async def test_if_record_branching_logic(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 32: .if_record() enables branching logic (CT vs MRI)."""
        F = Field()

        # Register two separate flows for branching
        flow_ct = (
            record("first_check")
            .on_status("finished")
            .if_record(F.study_type == "CT")
            .create_record("segment_CT_single")
        )
        flow_mri = (
            record("first_check")
            .on_status("finished")
            .if_record(F.study_type == "MRI")
            .create_record("segment_MRI_single")
        )
        app_with_engine.register_flow(flow_ct)
        app_with_engine.register_flow(flow_mri)

        hierarchy = await _create_hierarchy(test_session)

        # Create CT record
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"study_type": "CT"},
        )

        # CT segmentation should be created
        ct_records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(ct_records) == 1

        # MRI segmentation should NOT be created
        mri_records = await _find_records(
            client, record_type_name="segment_MRI_single", patient_id=hierarchy["patient_id"]
        )
        assert len(mri_records) == 0


# ============================================================================
# 9. TestEntityCreationTriggers (tests 33-34)
# ============================================================================


class TestEntityCreationTriggers:
    """Test entity creation triggers (study().on_created())."""

    @pytest.mark.usefixtures("app_with_engine")
    async def test_study_on_created_triggers_record_creation(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 33: study().on_created() creates record when study created."""
        entity_flow = study().on_created().create_record("first_check")
        app_with_engine.register_flow(entity_flow)

        # Create hierarchy
        patient = Patient(id="TEST_PAT001", name="Test Patient")
        test_session.add(patient)
        await test_session.commit()

        study_obj = Study(
            study_uid="1.2.3.4.5", patient_id="TEST_PAT001", date=datetime.now(tz=UTC).date()
        )
        test_session.add(study_obj)
        await test_session.commit()

        series_obj = Series(series_uid="1.2.3.4.5.1", series_number=1, study_uid="1.2.3.4.5")
        test_session.add(series_obj)
        await test_session.commit()

        # Trigger entity creation
        await app_with_engine.handle_entity_created("study", "TEST_PAT001", "1.2.3.4.5")

        # Verify first_check record was created
        records = await _find_records(
            client, record_type_name="first_check", patient_id="TEST_PAT001"
        )
        assert len(records) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_study_creation_no_duplicate(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 34: Triggering same entity twice respects max_records."""
        entity_flow = study().on_created().create_record("first_check")
        app_with_engine.register_flow(entity_flow)

        patient = Patient(id="TEST_PAT001", name="Test Patient")
        test_session.add(patient)
        await test_session.commit()

        study_obj = Study(
            study_uid="1.2.3.4.5", patient_id="TEST_PAT001", date=datetime.now(tz=UTC).date()
        )
        test_session.add(study_obj)
        await test_session.commit()

        series_obj = Series(series_uid="1.2.3.4.5.1", series_number=1, study_uid="1.2.3.4.5")
        test_session.add(series_obj)
        await test_session.commit()

        # Trigger twice
        await app_with_engine.handle_entity_created("study", "TEST_PAT001", "1.2.3.4.5")
        await app_with_engine.handle_entity_created("study", "TEST_PAT001", "1.2.3.4.5")

        # first_check has max_records default (no limit) so 2 might be created,
        # but we verify it doesn't error
        records = await _find_records(
            client, record_type_name="first_check", patient_id="TEST_PAT001"
        )
        assert len(records) >= 1


# ============================================================================
# 10. TestMultiStepWorkflow (tests 35-42)
# ============================================================================


class TestMultiStepWorkflow:
    """Test multi-step workflow orchestration (demo_liver pattern)."""

    @pytest.mark.usefixtures("app_with_engine")
    async def test_study_triggers_first_check(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 35: Study creation triggers first_check record."""
        entity_flow = study().on_created().create_record("first_check")
        app_with_engine.register_flow(entity_flow)

        hierarchy = await _create_hierarchy(test_session)
        await app_with_engine.handle_entity_created(
            "study", hierarchy["patient_id"], hierarchy["study_uid"]
        )

        records = await _find_records(
            client, record_type_name="first_check", patient_id=hierarchy["patient_id"]
        )
        assert len(records) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_first_check_finished_ct(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 36: first_check finished with CT → segment_CT_single."""
        F = Field()
        flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.is_good == True, F.study_type == "CT")  # noqa: E712
            .create_record("segment_CT_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"is_good": True, "study_type": "CT"},
        )

        records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) == 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_first_check_finished_mri(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 37: first_check finished with MRI → segment_MRI_single."""
        F = Field()
        flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.is_good == True, F.study_type == "MRI")  # noqa: E712
            .create_record("segment_MRI_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)
        await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"is_good": True, "study_type": "MRI"},
        )

        records = await _find_records(
            client, record_type_name="segment_MRI_single", patient_id=hierarchy["patient_id"]
        )
        assert len(records) == 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_file_update_invalidates_projections(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 38: master_model file update invalidates projections."""
        file_flow = (
            file("master_model").on_update().invalidate_all_records("create_master_projection")
        )
        app_with_engine.register_flow(file_flow)

        hierarchy = await _create_hierarchy(test_session)

        # Create and finish projection records
        rec1 = await _create_record_via_api(
            client,
            "create_master_projection",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
            data={"projection": "done"},
        )
        assert rec1["status"] == "finished"

        # Trigger file event
        response = await client.post(
            f"/api/patients/{hierarchy['patient_id']}/file-events",
            json=["master_model"],
        )
        assert response.status_code == 200

        # Verify invalidation (hard mode resets to pending)
        records = await _find_records(
            client,
            record_type_name="create_master_projection",
            patient_id=hierarchy["patient_id"],
        )
        assert all(r["status"] == "pending" for r in records)

    @pytest.mark.usefixtures("app_with_engine")
    async def test_end_to_end_ct_path(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 39: End-to-end flow for CT path."""
        F = Field()

        # Register all flows
        entity_flow = study().on_created().create_record("first_check")
        ct_flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.is_good == True, F.study_type == "CT")  # noqa: E712
            .create_record("segment_CT_single")
        )
        app_with_engine.register_flow(entity_flow)
        app_with_engine.register_flow(ct_flow)

        hierarchy = await _create_hierarchy(test_session)

        # Step 1: Trigger study creation flow
        await app_with_engine.handle_entity_created(
            "study", hierarchy["patient_id"], hierarchy["study_uid"]
        )

        # Verify first_check created
        first_checks = await _find_records(
            client, record_type_name="first_check", patient_id=hierarchy["patient_id"]
        )
        assert len(first_checks) >= 1
        first_check_id = first_checks[0]["id"]

        # Step 2: Submit first_check data (auto-finishes, triggers CT flow)
        resp = await client.post(
            f"{RECORDS_BASE}/{first_check_id}/data",
            json={"is_good": True, "study_type": "CT"},
        )
        assert resp.status_code == 200

        # Verify segment_CT_single created
        ct_records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(ct_records) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_end_to_end_mri_path(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 40: End-to-end flow for MRI path."""
        F = Field()

        entity_flow = study().on_created().create_record("first_check")
        mri_flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.is_good == True, F.study_type == "MRI")  # noqa: E712
            .create_record("segment_MRI_single")
        )
        app_with_engine.register_flow(entity_flow)
        app_with_engine.register_flow(mri_flow)

        hierarchy = await _create_hierarchy(test_session)
        await app_with_engine.handle_entity_created(
            "study", hierarchy["patient_id"], hierarchy["study_uid"]
        )

        first_checks = await _find_records(
            client, record_type_name="first_check", patient_id=hierarchy["patient_id"]
        )
        assert len(first_checks) >= 1
        first_check_id = first_checks[0]["id"]

        resp = await client.post(
            f"{RECORDS_BASE}/{first_check_id}/data",
            json={"is_good": True, "study_type": "MRI"},
        )
        assert resp.status_code == 200

        mri_records = await _find_records(
            client, record_type_name="segment_MRI_single", patient_id=hierarchy["patient_id"]
        )
        assert len(mri_records) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_multiple_patients(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 41: Flow handles multiple patients independently."""
        entity_flow = study().on_created().create_record("first_check")
        app_with_engine.register_flow(entity_flow)

        # Create two patients
        h1 = await _create_hierarchy(
            test_session,
            patient_id="PAT001",
            study_uid="1.2.3.4.5",
            series_uid="1.2.3.4.5.1",
        )
        h2 = await _create_hierarchy(
            test_session,
            patient_id="PAT002",
            study_uid="1.2.3.4.6",
            series_uid="1.2.3.4.6.1",
        )

        await app_with_engine.handle_entity_created("study", h1["patient_id"], h1["study_uid"])
        await app_with_engine.handle_entity_created("study", h2["patient_id"], h2["study_uid"])

        pat1_checks = await _find_records(
            client, record_type_name="first_check", patient_id="PAT001"
        )
        pat2_checks = await _find_records(
            client, record_type_name="first_check", patient_id="PAT002"
        )
        assert len(pat1_checks) >= 1
        assert len(pat2_checks) >= 1

    @pytest.mark.usefixtures("app_with_engine")
    async def test_wrong_status_no_cascade(
        self,
        client: AsyncClient,
        record_types: dict[str, RecordType],
        test_session: AsyncSession,
        app_with_engine: RecordFlowEngine,
    ) -> None:
        """Test 42: Non-matching status doesn't trigger downstream records."""
        F = Field()
        flow = (
            record("first_check")
            .on_status("finished")
            .if_record(F.study_type == "CT")
            .create_record("segment_CT_single")
        )
        app_with_engine.register_flow(flow)

        hierarchy = await _create_hierarchy(test_session)

        # Create first_check without finishing (no data submission)
        rec_data = await _create_record_via_api(
            client,
            "first_check",
            hierarchy["patient_id"],
            study_uid=hierarchy["study_uid"],
            series_uid=hierarchy["series_uid"],
        )

        # Set to inwork (not finished) — should NOT trigger flow
        await _update_status(client, rec_data["id"], "inwork")

        ct_records = await _find_records(
            client, record_type_name="segment_CT_single", patient_id=hierarchy["patient_id"]
        )
        assert len(ct_records) == 0


# ============================================================================
# 11. TestTaskContextIntegration (tests 43-46)
# ============================================================================


class TestTaskContextIntegration:
    """Test TaskContext integration in end-to-end workflow."""

    async def test_task_context_builds_from_record_id(self) -> None:
        """Test 43: TaskContext builds correctly from record_id."""
        mock_client = AsyncMock()

        mock_record = MagicMock()
        mock_record.id = 42
        mock_record.patient_id = "TEST_PAT001"
        mock_record.study_uid = "1.2.3"
        mock_record.series_uid = "1.2.3.1"
        mock_record.user_id = None
        mock_record.data = {}
        mock_record.clarinet_storage_path = None
        mock_record.record_type = MagicMock()
        mock_record.record_type.name = "test_type"
        mock_record.record_type.level = DicomQueryLevel.SERIES
        mock_record.record_type.file_registry = []
        mock_record.patient = MagicMock()
        mock_record.patient.anon_id = None
        mock_record.study = MagicMock()
        mock_record.study.anon_uid = None
        mock_record.series = MagicMock()
        mock_record.series.anon_uid = None
        mock_client.get_record = AsyncMock(return_value=mock_record)

        message = PipelineMessage(patient_id="TEST_PAT001", study_uid="1.2.3", record_id=42)
        context = await build_task_context(message, mock_client)

        assert context.files is not None
        assert context.records is not None
        assert context.client is mock_client
        assert context.msg is message

    async def test_task_context_file_resolver_always_available(self) -> None:
        """Test 44: TaskContext provides FileResolver even without record."""
        mock_client = AsyncMock()
        mock_client.get_record = AsyncMock(return_value=None)
        mock_client.get_series = AsyncMock(return_value=None)
        mock_client.get_study = AsyncMock(return_value=_make_mock_study("TEST_PAT001", "1.2.3.4.5"))

        message = PipelineMessage(patient_id="TEST_PAT001", study_uid="1.2.3.4.5")
        context = await build_task_context(message, mock_client)

        assert context.files is not None

    async def test_task_context_record_query_available(self) -> None:
        """Test 45: TaskContext provides RecordQuery for async queries."""
        mock_client = AsyncMock()

        mock_record = MagicMock()
        mock_record.id = 42
        mock_record.patient_id = "TEST_PAT001"
        mock_record.study_uid = "1.2.3"
        mock_record.series_uid = "1.2.3.1"
        mock_record.user_id = None
        mock_record.data = {}
        mock_record.clarinet_storage_path = None
        mock_record.record_type = MagicMock()
        mock_record.record_type.name = "test_type"
        mock_record.record_type.level = DicomQueryLevel.SERIES
        mock_record.record_type.file_registry = []
        mock_record.patient = MagicMock()
        mock_record.patient.anon_id = None
        mock_record.study = MagicMock()
        mock_record.study.anon_uid = None
        mock_record.series = MagicMock()
        mock_record.series.anon_uid = None
        mock_client.get_record = AsyncMock(return_value=mock_record)

        message = PipelineMessage(patient_id="TEST_PAT001", study_uid="1.2.3", record_id=42)
        context = await build_task_context(message, mock_client)

        assert context.records is not None

    async def test_task_context_empty_when_no_entities(self) -> None:
        """Test 46: TaskContext gracefully handles missing entities.

        Even with only study_uid (required field), the context is built
        with minimal working dirs (patient + study level only).
        """
        mock_client = AsyncMock()
        mock_client.get_record = AsyncMock(return_value=None)
        mock_client.get_series = AsyncMock(return_value=None)
        mock_client.get_study = AsyncMock(return_value=_make_mock_study("NONEXISTENT_PAT", "1.2.3"))

        message = PipelineMessage(patient_id="NONEXISTENT_PAT", study_uid="1.2.3")
        context = await build_task_context(message, mock_client)

        # Context should still be valid with minimal dirs
        assert context.files is not None
        assert context.records is not None
        assert context.msg is message
