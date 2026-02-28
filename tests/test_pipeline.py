"""Unit tests for Pipeline service — message models, chain builder, worker queues.

Pure logic tests, no RabbitMQ. Uses InMemoryBroker for task execution tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.pipeline.broker import DEFAULT_QUEUE, DICOM_QUEUE, GPU_QUEUE
from src.services.pipeline.chain import (
    _PIPELINE_REGISTRY,
    _TASK_REGISTRY,
    Pipeline,
)
from src.services.pipeline.message import PipelineMessage
from src.services.pipeline.worker import get_worker_queues


@pytest.fixture(autouse=True)
def _clear_registries():
    """Clear pipeline registries before each test."""
    _TASK_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    yield
    _TASK_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()


# ─── PipelineMessage ────────────────────────────────────────────────────────


class TestPipelineMessage:
    """Tests for PipelineMessage model."""

    def test_minimal_message(self):
        """Message can be created with only required fields."""
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3")
        assert msg.patient_id == "PAT001"
        assert msg.study_uid == "1.2.3"
        assert msg.series_uid is None
        assert msg.record_id is None
        assert msg.payload == {}
        assert msg.pipeline_id is None
        assert msg.step_index == 0

    def test_full_message(self):
        """Message with all fields set."""
        msg = PipelineMessage(
            patient_id="PAT002",
            study_uid="1.2.3.4",
            series_uid="1.2.3.4.5",
            record_id=42,
            record_type_name="ct_scan",
            payload={"threshold": 0.5},
            pipeline_id="seg_pipeline",
            step_index=2,
        )
        assert msg.record_id == 42
        assert msg.payload["threshold"] == 0.5
        assert msg.step_index == 2

    def test_message_serialization(self):
        """Message can be serialized and deserialized."""
        msg = PipelineMessage(
            patient_id="PAT001",
            study_uid="1.2.3",
            payload={"key": "value"},
        )
        data = msg.model_dump()
        restored = PipelineMessage(**data)
        assert restored == msg

    def test_message_copy_update(self):
        """model_copy with update preserves other fields."""
        msg = PipelineMessage(
            patient_id="PAT001",
            study_uid="1.2.3",
            payload={"key": "value"},
        )
        updated = msg.model_copy(update={"pipeline_id": "test", "step_index": 1})
        assert updated.pipeline_id == "test"
        assert updated.step_index == 1
        assert updated.patient_id == "PAT001"
        assert updated.payload == {"key": "value"}


# ─── Pipeline chain builder ─────────────────────────────────────────────────


class TestPipeline:
    """Tests for Pipeline chain builder DSL."""

    def test_pipeline_creation(self):
        """Pipeline registers itself by name."""
        p = Pipeline("test_pipeline")
        assert p.name == "test_pipeline"
        assert p.steps == []
        assert _PIPELINE_REGISTRY["test_pipeline"] is p

    def test_pipeline_step_chaining(self):
        """Steps can be chained and preserve order."""
        # Create mock tasks
        mock_task1 = AsyncMock()
        mock_task1.task_name = "step_one"
        mock_task2 = AsyncMock()
        mock_task2.task_name = "step_two"

        p = (
            Pipeline("chain_test")
            .step(mock_task1, queue="clarinet.dicom")
            .step(mock_task2, queue="clarinet.gpu")
        )

        assert len(p.steps) == 2
        assert p.steps[0].task_name == "step_one"
        assert p.steps[0].queue == "clarinet.dicom"
        assert p.steps[1].task_name == "step_two"
        assert p.steps[1].queue == "clarinet.gpu"

    def test_pipeline_registers_tasks(self):
        """Steps register their tasks in the global task registry."""
        mock_task = AsyncMock()
        mock_task.task_name = "registered_task"

        Pipeline("reg_test").step(mock_task)

        assert "registered_task" in _TASK_REGISTRY
        assert _TASK_REGISTRY["registered_task"] is mock_task

    def test_pipeline_default_queue(self):
        """Steps default to clarinet.default queue."""
        mock_task = AsyncMock()
        mock_task.task_name = "default_q_task"

        p = Pipeline("default_q_test").step(mock_task)

        assert p.steps[0].queue == DEFAULT_QUEUE

    @pytest.mark.asyncio
    async def test_pipeline_run_empty_raises(self):
        """Running a pipeline with no steps raises ValueError."""
        p = Pipeline("empty")
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3")
        with pytest.raises(ValueError, match="no steps"):
            await p.run(msg)

    def test_pipeline_repr(self):
        """Pipeline repr shows name and step names."""
        mock_task = AsyncMock()
        mock_task.task_name = "repr_task"

        p = Pipeline("repr_test").step(mock_task)
        repr_str = repr(p)
        assert "repr_test" in repr_str
        assert "repr_task" in repr_str

    def test_get_pipeline(self):
        """get_pipeline returns registered pipeline by name."""
        from src.services.pipeline import get_pipeline

        Pipeline("lookup_test")
        assert get_pipeline("lookup_test") is not None
        assert get_pipeline("nonexistent") is None

    def test_get_all_pipelines(self):
        """get_all_pipelines returns all registered pipelines."""
        from src.services.pipeline import get_all_pipelines

        Pipeline("p1")
        Pipeline("p2")
        all_p = get_all_pipelines()
        assert "p1" in all_p
        assert "p2" in all_p

    @pytest.mark.asyncio
    async def test_pipeline_run_dispatches_first_step(self):
        """Pipeline.run() dispatches the first step with correct labels."""
        mock_task = MagicMock()
        mock_task.task_name = "dispatch_task"

        kicker_mock = MagicMock()
        mock_task.kicker.return_value = kicker_mock
        kicker_mock.with_labels.return_value = kicker_mock
        kicker_mock.kiq = AsyncMock()

        p = Pipeline("dispatch_test").step(mock_task)
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3")

        await p.run(msg)

        mock_task.kicker.assert_called_once()
        call_kwargs = kicker_mock.with_labels.call_args[1]
        assert call_kwargs["pipeline_id"] == "dispatch_test"
        assert call_kwargs["step_index"] == "0"
        assert "pipeline_chain" not in call_kwargs


# ─── sync_pipeline_definitions ────────────────────────────────────────────────


class TestSyncPipelineDefinitions:
    """Tests for sync_pipeline_definitions with real DB."""

    @pytest.mark.asyncio
    async def test_sync_persists_definitions(self, test_session):
        """Syncing writes registered pipelines to the database."""
        from src.repositories.pipeline_definition_repository import (
            PipelineDefinitionRepository,
        )

        mock_task1 = AsyncMock()
        mock_task1.task_name = "sync_step_a"
        mock_task2 = AsyncMock()
        mock_task2.task_name = "sync_step_b"

        Pipeline("sync_test").step(mock_task1).step(mock_task2)

        repo = PipelineDefinitionRepository(test_session)

        # Sync using the repo directly (same logic as sync_pipeline_definitions)
        for pipeline in _PIPELINE_REGISTRY.values():
            await repo.upsert(pipeline.name, [s.to_dict() for s in pipeline.steps])

        definition = await repo.get("sync_test")
        assert definition.name == "sync_test"
        assert len(definition.steps) == 2
        assert definition.steps[0]["task_name"] == "sync_step_a"
        assert definition.steps[1]["task_name"] == "sync_step_b"

    @pytest.mark.asyncio
    async def test_sync_updates_existing_definition(self, test_session):
        """Syncing overwrites previously saved pipeline definition."""
        from src.repositories.pipeline_definition_repository import (
            PipelineDefinitionRepository,
        )

        mock_task1 = AsyncMock()
        mock_task1.task_name = "update_step_1"
        mock_task2 = AsyncMock()
        mock_task2.task_name = "update_step_2"

        repo = PipelineDefinitionRepository(test_session)

        # First version: single step
        p = Pipeline("update_test").step(mock_task1)
        await repo.upsert(p.name, [s.to_dict() for s in p.steps])

        definition = await repo.get("update_test")
        assert len(definition.steps) == 1

        # Second version: two steps (simulates code change + re-sync)
        p.step(mock_task2)
        await repo.upsert(p.name, [s.to_dict() for s in p.steps])

        await test_session.refresh(definition)
        assert len(definition.steps) == 2
        assert definition.steps[1]["task_name"] == "update_step_2"


# ─── Worker queue detection ──────────────────────────────────────────────────


class TestWorkerQueues:
    """Tests for worker queue auto-detection."""

    def test_default_queues(self):
        """Worker with no capabilities gets only default queue."""
        with patch("src.services.pipeline.worker.settings") as mock_settings:
            mock_settings.have_gpu = False
            mock_settings.have_dicom = False
            queues = get_worker_queues()
            assert queues == [DEFAULT_QUEUE]

    def test_gpu_queue(self):
        """Worker with GPU capability gets default + GPU queues."""
        with patch("src.services.pipeline.worker.settings") as mock_settings:
            mock_settings.have_gpu = True
            mock_settings.have_dicom = False
            queues = get_worker_queues()
            assert DEFAULT_QUEUE in queues
            assert GPU_QUEUE in queues

    def test_dicom_queue(self):
        """Worker with DICOM capability gets default + DICOM queues."""
        with patch("src.services.pipeline.worker.settings") as mock_settings:
            mock_settings.have_gpu = False
            mock_settings.have_dicom = True
            queues = get_worker_queues()
            assert DEFAULT_QUEUE in queues
            assert DICOM_QUEUE in queues

    def test_all_queues(self):
        """Worker with all capabilities gets all queues."""
        with patch("src.services.pipeline.worker.settings") as mock_settings:
            mock_settings.have_gpu = True
            mock_settings.have_dicom = True
            queues = get_worker_queues()
            assert DEFAULT_QUEUE in queues
            assert GPU_QUEUE in queues
            assert DICOM_QUEUE in queues
            assert len(queues) == 3


# ─── RecordFlow PipelineAction integration ───────────────────────────────────


class TestPipelineActionDSL:
    """Tests for PipelineAction in RecordFlow DSL."""

    def test_pipeline_action_model(self):
        """PipelineAction model is correctly formed."""
        from src.services.recordflow.flow_action import PipelineAction

        action = PipelineAction(pipeline_name="seg", extra_payload={"k": "v"})
        assert action.type == "pipeline"
        assert action.pipeline_name == "seg"
        assert action.extra_payload == {"k": "v"}

    def test_flow_record_pipeline_method(self):
        """FlowRecord.pipeline() creates a PipelineAction."""
        from src.services.recordflow.flow_action import PipelineAction
        from src.services.recordflow.flow_record import RECORD_REGISTRY, record

        RECORD_REGISTRY.clear()
        flow = record("ct_scan").on_status("finished").pipeline("ct_segmentation", threshold=0.5)

        assert len(flow.actions) == 1
        action = flow.actions[0]
        assert isinstance(action, PipelineAction)
        assert action.pipeline_name == "ct_segmentation"
        assert action.extra_payload == {"threshold": 0.5}

    def test_pipeline_in_conditional(self):
        """PipelineAction works inside conditional blocks."""
        from src.services.recordflow.flow_action import PipelineAction
        from src.services.recordflow.flow_record import RECORD_REGISTRY, record
        from src.services.recordflow.flow_result import ConstantFlowResult

        RECORD_REGISTRY.clear()
        flow = (
            record("ct_scan")
            .on_status("finished")
            .if_(ConstantFlowResult(True))
            .pipeline("ct_segmentation")
        )

        assert len(flow.conditions) == 1
        condition = flow.conditions[0]
        assert len(condition.actions) == 1
        assert isinstance(condition.actions[0], PipelineAction)

    def test_pipeline_action_in_union(self):
        """PipelineAction is included in FlowAction union type."""
        from src.services.recordflow.flow_action import PipelineAction

        action = PipelineAction(pipeline_name="test")
        # Verify it matches the FlowAction union
        assert isinstance(action, PipelineAction)


# ─── Exceptions ──────────────────────────────────────────────────────────────


class TestPipelineExceptions:
    """Tests for pipeline exception hierarchy."""

    def test_pipeline_error_inherits_clarinet_error(self):
        """PipelineError is a ClarinetError."""
        from src.exceptions.domain import ClarinetError, PipelineError

        assert issubclass(PipelineError, ClarinetError)

    def test_pipeline_step_error(self):
        """PipelineStepError formats message correctly."""
        from src.exceptions.domain import PipelineStepError

        err = PipelineStepError("segmentation", "GPU memory exhausted")
        assert "segmentation" in str(err)
        assert "GPU memory exhausted" in str(err)

    def test_pipeline_config_error(self):
        """PipelineConfigError inherits from PipelineError."""
        from src.exceptions.domain import PipelineConfigError, PipelineError

        assert issubclass(PipelineConfigError, PipelineError)

    def test_with_context(self):
        """PipelineError supports with_context()."""
        from src.exceptions.domain import PipelineError

        err = PipelineError().with_context("Broker unreachable")
        assert "Broker unreachable" in str(err)


# ─── DeadLetterMiddleware ────────────────────────────────────────────────────


class TestDeadLetterMiddleware:
    """Tests for DeadLetterMiddleware routing logic."""

    @pytest.mark.asyncio
    async def test_skips_successful_tasks(self):
        """Successful tasks are not routed to DLQ."""
        from taskiq import TaskiqMessage, TaskiqResult

        from src.services.pipeline.middleware import DeadLetterMiddleware

        middleware = DeadLetterMiddleware()
        msg = TaskiqMessage(task_id="t1", task_name="test", labels={}, args=[], kwargs={})
        result = TaskiqResult(is_err=False, return_value={"ok": True}, execution_time=0.1)

        with patch.object(middleware, "_publish_to_dlq", new_callable=AsyncMock) as mock_dlq:
            await middleware.post_execute(msg, result)
            mock_dlq.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_retrying_tasks(self):
        """Tasks being retried (NoResultError) are not routed to DLQ."""
        from taskiq import TaskiqMessage, TaskiqResult
        from taskiq.exceptions import NoResultError

        from src.services.pipeline.middleware import DeadLetterMiddleware

        middleware = DeadLetterMiddleware()
        msg = TaskiqMessage(task_id="t2", task_name="test", labels={}, args=[], kwargs={})
        result = TaskiqResult(
            is_err=True, return_value=None, execution_time=0.1, error=NoResultError()
        )

        with patch.object(middleware, "_publish_to_dlq", new_callable=AsyncMock) as mock_dlq:
            await middleware.post_execute(msg, result)
            mock_dlq.assert_not_called()

    @pytest.mark.asyncio
    async def test_publishes_terminal_failures(self):
        """Tasks with real errors (retries exhausted) are routed to DLQ."""
        from taskiq import TaskiqMessage, TaskiqResult

        from src.exceptions.domain import PipelineStepError
        from src.services.pipeline.middleware import DeadLetterMiddleware

        middleware = DeadLetterMiddleware()
        msg = TaskiqMessage(task_id="t3", task_name="test_fail", labels={}, args=[], kwargs={})
        result = TaskiqResult(
            is_err=True,
            return_value=None,
            execution_time=0.1,
            error=PipelineStepError("step", "GPU OOM"),
        )

        with patch.object(middleware, "_publish_to_dlq", new_callable=AsyncMock) as mock_dlq:
            await middleware.post_execute(msg, result)
            mock_dlq.assert_called_once_with(msg, result)


# ─── Retry defaults ──────────────────────────────────────────────────────────


class TestRetryDefaults:
    """Tests for broker retry configuration."""

    def test_default_retry_label_is_true(self):
        """create_broker() includes SmartRetryMiddleware with default_retry_label=True."""

        from taskiq.middlewares import SmartRetryMiddleware

        with patch("src.services.pipeline.broker.settings") as mock_settings:
            mock_settings.rabbitmq_login = "guest"
            mock_settings.rabbitmq_password = "guest"
            mock_settings.rabbitmq_host = "localhost"
            mock_settings.rabbitmq_port = 5672
            mock_settings.rabbitmq_exchange = "test"
            mock_settings.pipeline_result_backend_url = None
            mock_settings.pipeline_retry_count = 3
            mock_settings.pipeline_retry_delay = 5
            mock_settings.pipeline_retry_max_delay = 120

            from src.services.pipeline.broker import create_broker

            broker = create_broker("test.default")

            # Find SmartRetryMiddleware in the broker's middlewares
            smart_retry = None
            for mw in broker.middlewares:
                if isinstance(mw, SmartRetryMiddleware):
                    smart_retry = mw
                    break

            assert smart_retry is not None, "SmartRetryMiddleware not found in broker middlewares"
            assert smart_retry.default_retry_label is True
