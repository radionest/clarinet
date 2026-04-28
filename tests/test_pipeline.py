"""Unit tests for Pipeline service — message models, chain builder, worker queues.

Pure logic tests, no RabbitMQ. Uses InMemoryBroker for task execution tests.
"""

from __future__ import annotations

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clarinet.exceptions.domain import PipelineConfigError
from clarinet.services.pipeline.broker import reset_brokers
from clarinet.services.pipeline.chain import (
    _PIPELINE_REGISTRY,
    _TASK_REGISTRY,
    Pipeline,
    persist_definitions,
)
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.worker import get_worker_queues
from clarinet.settings import settings

# Module-level snapshots of the namespaced queue names. Captured at
# import time — tests that monkeypatch ``settings.project_name`` must use
# ``settings.X_queue_name`` directly instead of these constants.
DEFAULT_QUEUE = settings.default_queue_name
GPU_QUEUE = settings.gpu_queue_name
DICOM_QUEUE = settings.dicom_queue_name


@pytest.fixture(autouse=True)
def _clear_registries():
    """Clear pipeline + broker registries before each test."""
    _TASK_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    reset_brokers()
    yield
    _TASK_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    reset_brokers()


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
            record_type_name="ct-scan",
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
        # Create mock tasks. _pipeline_queue must be an explicit string —
        # bare AsyncMock auto-creates a MagicMock attribute that breaks
        # the queue-resolution invariant in PipelineStep.
        mock_task1 = AsyncMock()
        mock_task1.task_name = "step_one"
        mock_task1._pipeline_queue = DICOM_QUEUE
        mock_task2 = AsyncMock()
        mock_task2.task_name = "step_two"
        mock_task2._pipeline_queue = GPU_QUEUE

        p = (
            Pipeline("chain_test")
            .step(mock_task1, queue=DICOM_QUEUE)
            .step(mock_task2, queue=GPU_QUEUE)
        )

        assert len(p.steps) == 2
        assert p.steps[0].task_name == "step_one"
        assert p.steps[0].queue == DICOM_QUEUE
        assert p.steps[1].task_name == "step_two"
        assert p.steps[1].queue == GPU_QUEUE

    def test_pipeline_registers_tasks(self):
        """Steps register their tasks in the global task registry."""
        mock_task = AsyncMock()
        mock_task.task_name = "registered_task"
        mock_task._pipeline_queue = None

        Pipeline("reg_test").step(mock_task)

        assert "registered_task" in _TASK_REGISTRY
        assert _TASK_REGISTRY["registered_task"] is mock_task

    def test_pipeline_default_queue(self):
        """Steps default to settings.default_queue_name when task has none."""
        mock_task = AsyncMock()
        mock_task.task_name = "default_q_task"
        mock_task._pipeline_queue = None

        p = Pipeline("default_q_test").step(mock_task)

        assert p.steps[0].queue == DEFAULT_QUEUE

    def test_pipeline_step_uses_task_bound_queue_when_present(self):
        """step() without queue= picks up task._pipeline_queue."""
        mock_task = AsyncMock()
        mock_task.task_name = "bound_only_task"
        mock_task._pipeline_queue = GPU_QUEUE

        p = Pipeline("implicit_queue").step(mock_task)

        assert p.steps[0].queue == GPU_QUEUE

    def test_pipeline_step_queue_override_raises_on_mismatch(self):
        """Specifying a queue that conflicts with the task's bound queue raises."""
        mock_task = AsyncMock()
        mock_task.task_name = "bound_task"
        mock_task._pipeline_queue = GPU_QUEUE

        with pytest.raises(PipelineConfigError, match="bound_task"):
            Pipeline("override_test").step(mock_task, queue=DICOM_QUEUE)

    @pytest.mark.asyncio
    async def test_pipeline_run_empty_raises(self):
        """Running a pipeline with no steps raises PipelineConfigError."""
        p = Pipeline("empty")
        msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3")
        with pytest.raises(PipelineConfigError, match="no steps"):
            await p.run(msg)

    def test_pipeline_repr(self):
        """Pipeline repr shows name and step names."""
        mock_task = AsyncMock()
        mock_task.task_name = "repr_task"
        mock_task._pipeline_queue = None

        p = Pipeline("repr_test").step(mock_task)
        repr_str = repr(p)
        assert "repr_test" in repr_str
        assert "repr_task" in repr_str

    def test_get_pipeline(self):
        """get_pipeline returns registered pipeline by name."""
        from clarinet.services.pipeline import get_pipeline

        Pipeline("lookup_test")
        assert get_pipeline("lookup_test") is not None
        assert get_pipeline("nonexistent") is None

    def test_get_all_pipelines(self):
        """get_all_pipelines returns all registered pipelines."""
        from clarinet.services.pipeline import get_all_pipelines

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
        mock_task._pipeline_queue = None

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
        assert call_kwargs["queue"] == DEFAULT_QUEUE
        assert "pipeline_chain" not in call_kwargs
        assert "routing_key" not in call_kwargs


# ─── sync_pipeline_definitions ────────────────────────────────────────────────


class TestSyncPipelineDefinitions:
    """Tests for sync_pipeline_definitions with real DB."""

    @pytest.mark.asyncio
    async def test_sync_persists_definitions(self, test_session):
        """Syncing writes registered pipelines to the database."""
        from clarinet.repositories.pipeline_definition_repository import (
            PipelineDefinitionRepository,
        )

        mock_task1 = AsyncMock()
        mock_task1.task_name = "sync_step_a"
        mock_task1._pipeline_queue = None
        mock_task2 = AsyncMock()
        mock_task2.task_name = "sync_step_b"
        mock_task2._pipeline_queue = None

        Pipeline("sync_test").step(mock_task1).step(mock_task2)

        repo = PipelineDefinitionRepository(test_session)
        await persist_definitions(repo)

        definition = await repo.get("sync_test")
        assert definition.name == "sync_test"
        assert len(definition.steps) == 2
        assert definition.steps[0]["task_name"] == "sync_step_a"
        assert definition.steps[1]["task_name"] == "sync_step_b"

    @pytest.mark.asyncio
    async def test_sync_updates_existing_definition(self, test_session):
        """Syncing overwrites previously saved pipeline definition."""
        from clarinet.repositories.pipeline_definition_repository import (
            PipelineDefinitionRepository,
        )

        mock_task1 = AsyncMock()
        mock_task1.task_name = "update_step_1"
        mock_task1._pipeline_queue = None
        mock_task2 = AsyncMock()
        mock_task2.task_name = "update_step_2"
        mock_task2._pipeline_queue = None

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

    def test_default_queues(self, monkeypatch):
        """Worker with no capabilities gets only default queue."""
        monkeypatch.setattr(settings, "have_gpu", False)
        monkeypatch.setattr(settings, "have_dicom", False)
        queues = get_worker_queues()
        assert queues == [DEFAULT_QUEUE]

    def test_gpu_queue(self, monkeypatch):
        """Worker with GPU capability gets default + GPU queues."""
        monkeypatch.setattr(settings, "have_gpu", True)
        monkeypatch.setattr(settings, "have_dicom", False)
        queues = get_worker_queues()
        assert DEFAULT_QUEUE in queues
        assert GPU_QUEUE in queues

    def test_dicom_queue(self, monkeypatch):
        """Worker with DICOM capability gets default + DICOM queues."""
        monkeypatch.setattr(settings, "have_gpu", False)
        monkeypatch.setattr(settings, "have_dicom", True)
        queues = get_worker_queues()
        assert DEFAULT_QUEUE in queues
        assert DICOM_QUEUE in queues

    def test_all_queues(self, monkeypatch):
        """Worker with all capabilities gets all queues."""
        monkeypatch.setattr(settings, "have_gpu", True)
        monkeypatch.setattr(settings, "have_dicom", True)
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
        from clarinet.services.recordflow.flow_action import PipelineAction

        action = PipelineAction(pipeline_name="seg", extra_payload={"k": "v"})
        assert action.type == "pipeline"
        assert action.pipeline_name == "seg"
        assert action.extra_payload == {"k": "v"}

    def test_flow_record_pipeline_method(self):
        """FlowRecord.pipeline() creates a PipelineAction."""
        from clarinet.services.recordflow.flow_action import PipelineAction
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, record

        RECORD_REGISTRY.clear()
        flow = record("ct-scan").on_status("finished").pipeline("ct_segmentation", threshold=0.5)

        assert len(flow.actions) == 1
        action = flow.actions[0]
        assert isinstance(action, PipelineAction)
        assert action.pipeline_name == "ct_segmentation"
        assert action.extra_payload == {"threshold": 0.5}

    def test_pipeline_in_conditional(self):
        """PipelineAction works inside conditional blocks."""
        from clarinet.services.recordflow.flow_action import PipelineAction
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, record
        from clarinet.services.recordflow.flow_result import ConstantFlowResult

        RECORD_REGISTRY.clear()
        flow = (
            record("ct-scan")
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
        from clarinet.services.recordflow.flow_action import PipelineAction

        action = PipelineAction(pipeline_name="test")
        # Verify it matches the FlowAction union
        assert isinstance(action, PipelineAction)

    def test_do_task_creates_auto_pipeline(self):
        """FlowRecord.do_task() auto-creates a single-step Pipeline."""
        from clarinet.services.pipeline import get_pipeline
        from clarinet.services.recordflow.flow_action import PipelineAction
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, FlowRecord

        RECORD_REGISTRY.clear()

        mock_task = MagicMock()
        mock_task.task_name = "auto_pipeline_task"
        mock_task._pipeline_queue = None

        fr = FlowRecord("ct-scan")
        fr.on_status("finished").do_task(mock_task, threshold=0.5)

        # PipelineAction created with correct name
        assert len(fr.actions) == 1
        action = fr.actions[0]
        assert isinstance(action, PipelineAction)
        assert action.pipeline_name == "_task:auto_pipeline_task"
        assert action.extra_payload == {"threshold": 0.5}

        # Auto-Pipeline registered with one step
        pipeline = get_pipeline("_task:auto_pipeline_task")
        assert pipeline is not None
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].task is mock_task

    def test_do_task_preserves_gpu_queue(self):
        """do_task() passes _pipeline_queue to the auto-pipeline step."""
        from clarinet.services.pipeline import get_pipeline
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, FlowRecord

        RECORD_REGISTRY.clear()

        mock_task = MagicMock()
        mock_task.task_name = "ns:gpu_segmentation"
        mock_task._pipeline_queue = "clarinet.gpu"

        fr = FlowRecord("ct-scan")
        fr.on_status("finished").do_task(mock_task)

        pipeline = get_pipeline("_task:gpu_segmentation")
        assert pipeline is not None
        assert pipeline.steps[0].queue == "clarinet.gpu"

    def test_do_task_preserves_dicom_queue(self):
        """do_task() preserves clarinet.dicom queue from task attribute."""
        from clarinet.services.pipeline import get_pipeline
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, FlowRecord

        RECORD_REGISTRY.clear()

        mock_task = MagicMock()
        mock_task.task_name = "ns:dicom_fetch"
        mock_task._pipeline_queue = "clarinet.dicom"

        fr = FlowRecord("ct-scan")
        fr.on_status("finished").do_task(mock_task)

        pipeline = get_pipeline("_task:dicom_fetch")
        assert pipeline is not None
        assert pipeline.steps[0].queue == "clarinet.dicom"

    def test_do_task_defaults_queue_when_no_attribute(self):
        """do_task() uses DEFAULT_QUEUE when task has no _pipeline_queue."""
        from clarinet.services.pipeline import get_pipeline
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, FlowRecord

        RECORD_REGISTRY.clear()

        mock_task = MagicMock(spec=[])  # no auto-created attributes
        mock_task.task_name = "ns:plain_task"

        fr = FlowRecord("ct-scan")
        fr.on_status("finished").do_task(mock_task)

        pipeline = get_pipeline("_task:plain_task")
        assert pipeline is not None
        assert pipeline.steps[0].queue == DEFAULT_QUEUE

    def test_do_task_defaults_queue_when_none(self):
        """do_task() uses DEFAULT_QUEUE when _pipeline_queue is None."""
        from clarinet.services.pipeline import get_pipeline
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, FlowRecord

        RECORD_REGISTRY.clear()

        mock_task = MagicMock()
        mock_task.task_name = "ns:none_queue_task"
        mock_task._pipeline_queue = None

        fr = FlowRecord("ct-scan")
        fr.on_status("finished").do_task(mock_task)

        pipeline = get_pipeline("_task:none_queue_task")
        assert pipeline is not None
        assert pipeline.steps[0].queue == DEFAULT_QUEUE

    def test_do_task_dedup_preserves_queue(self):
        """Second do_task() call reuses the pipeline — queue stays correct."""
        from clarinet.services.pipeline import get_pipeline
        from clarinet.services.recordflow.flow_record import RECORD_REGISTRY, FlowRecord

        RECORD_REGISTRY.clear()

        mock_task = MagicMock()
        mock_task.task_name = "ns:dedup_gpu"
        mock_task._pipeline_queue = "clarinet.gpu"

        fr1 = FlowRecord("ct-scan")
        fr1.on_status("finished").do_task(mock_task)

        fr2 = FlowRecord("mri-scan")
        fr2.on_status("finished").do_task(mock_task)

        pipeline = get_pipeline("_task:dedup_gpu")
        assert pipeline is not None
        assert len(pipeline.steps) == 1
        assert pipeline.steps[0].queue == "clarinet.gpu"

    def test_pipeline_task_attribute_set(self):
        """@pipeline_task(queue=...) stores the queue as _pipeline_queue."""
        from clarinet.services.pipeline.task import pipeline_task

        @pipeline_task(queue=GPU_QUEUE)
        async def dummy_gpu_task(msg, ctx):
            pass

        assert dummy_gpu_task._pipeline_queue == GPU_QUEUE

    def test_pipeline_task_attribute_defaults_to_default_queue(self):
        """@pipeline_task() without queue stores settings.default_queue_name."""
        from clarinet.services.pipeline.task import pipeline_task

        @pipeline_task()
        async def dummy_default_task(msg, ctx):
            pass

        assert dummy_default_task._pipeline_queue == DEFAULT_QUEUE


# ─── Exceptions ──────────────────────────────────────────────────────────────


class TestPipelineExceptions:
    """Tests for pipeline exception hierarchy."""

    def test_pipeline_error_inherits_clarinet_error(self):
        """PipelineError is a ClarinetError."""
        from clarinet.exceptions.domain import ClarinetError, PipelineError

        assert issubclass(PipelineError, ClarinetError)

    def test_pipeline_step_error(self):
        """PipelineStepError formats message correctly."""
        from clarinet.exceptions.domain import PipelineStepError

        err = PipelineStepError("segmentation", "GPU memory exhausted")
        assert "segmentation" in str(err)
        assert "GPU memory exhausted" in str(err)

    def test_pipeline_config_error(self):
        """PipelineConfigError inherits from PipelineError."""
        from clarinet.exceptions.domain import PipelineConfigError, PipelineError

        assert issubclass(PipelineConfigError, PipelineError)

    def test_with_context(self):
        """PipelineError supports with_context()."""
        from clarinet.exceptions.domain import PipelineError

        err = PipelineError().with_context("Broker unreachable")
        assert "Broker unreachable" in str(err)


# ─── DeadLetterMiddleware ────────────────────────────────────────────────────


class TestDLQPublisher:
    """Tests for DLQPublisher shared AMQP publisher."""

    @pytest.mark.asyncio
    async def test_publish_without_startup_logs_error(self):
        """Publishing before startup() logs an error and does not raise."""
        from clarinet.services.pipeline.middleware import DLQPublisher

        publisher = DLQPublisher()

        with patch("clarinet.services.pipeline.middleware.logger") as mock_logger:
            await publisher.publish({"key": "value"})
            mock_logger.error.assert_called_once()
            assert "not initialized" in mock_logger.error.call_args[0][0]

    @pytest.mark.asyncio
    async def test_startup_idempotent(self):
        """Calling startup() twice only connects once."""
        from clarinet.services.pipeline.middleware import DLQPublisher

        publisher = DLQPublisher(amqp_url="amqp://guest:guest@localhost/")

        mock_connection = AsyncMock()
        mock_channel = AsyncMock()
        mock_connection.channel.return_value = mock_channel

        with patch(
            "aio_pika.connect_robust", new_callable=AsyncMock, return_value=mock_connection
        ) as mock_connect:
            await publisher.startup()
            await publisher.startup()
            mock_connect.assert_called_once()

        await publisher.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_clears_state(self):
        """shutdown() closes connection and resets state to None."""
        from clarinet.services.pipeline.middleware import DLQPublisher

        publisher = DLQPublisher()
        mock_connection = AsyncMock()
        publisher._connection = mock_connection
        publisher._channel = AsyncMock()

        await publisher.shutdown()

        mock_connection.close.assert_called_once()
        assert publisher._connection is None
        assert publisher._channel is None

    @pytest.mark.asyncio
    async def test_publish_sends_json_message(self):
        """publish() sends a JSON-encoded persistent message to the DLQ."""
        import json

        from clarinet.services.pipeline.middleware import DLQPublisher

        publisher = DLQPublisher(queue_name="clarinet.dead_letter")
        mock_channel = AsyncMock()
        publisher._channel = mock_channel

        payload = {"task_name": "test", "error": "boom"}
        await publisher.publish(payload)

        mock_channel.default_exchange.publish.assert_called_once()
        call_args = mock_channel.default_exchange.publish.call_args
        message = call_args[0][0]
        assert json.loads(message.body) == payload
        assert call_args[1]["routing_key"] == "clarinet.dead_letter"


class TestDeadLetterMiddleware:
    """Tests for DeadLetterMiddleware routing logic."""

    @pytest.mark.asyncio
    async def test_skips_successful_tasks(self):
        """Successful tasks are not routed to DLQ."""
        from taskiq import TaskiqMessage, TaskiqResult

        from clarinet.services.pipeline.middleware import DeadLetterMiddleware, DLQPublisher

        middleware = DeadLetterMiddleware(DLQPublisher())
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

        from clarinet.services.pipeline.middleware import DeadLetterMiddleware, DLQPublisher

        middleware = DeadLetterMiddleware(DLQPublisher())
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

        from clarinet.exceptions.domain import PipelineStepError
        from clarinet.services.pipeline.middleware import DeadLetterMiddleware, DLQPublisher

        middleware = DeadLetterMiddleware(DLQPublisher())
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
        """create_broker() includes RetryMiddleware with default_retry_label=True."""

        from clarinet.services.pipeline.middleware import RetryMiddleware

        with patch("clarinet.services.pipeline.broker.settings") as mock_settings:
            mock_settings.rabbitmq_login = "guest"
            mock_settings.rabbitmq_password = "guest"
            mock_settings.rabbitmq_host = "localhost"
            mock_settings.rabbitmq_port = 5672
            mock_settings.rabbitmq_exchange = "test"
            mock_settings.dlq_queue_name = "test.dead_letter"
            mock_settings.pipeline_result_backend_url = None
            mock_settings.pipeline_retry_count = 3
            mock_settings.pipeline_retry_delay = 5
            mock_settings.pipeline_retry_max_delay = 120

            from clarinet.services.pipeline.broker import create_broker

            broker = create_broker("test.default")

            # Find RetryMiddleware in the broker's middlewares
            retry_mw = None
            for mw in broker.middlewares:
                if isinstance(mw, RetryMiddleware):
                    retry_mw = mw
                    break

            assert retry_mw is not None, "RetryMiddleware not found in broker middlewares"
            assert retry_mw.default_retry_label is True


class TestRetryMiddleware:
    """Tests for RetryMiddleware — business error (4xx) skip logic."""

    @pytest.mark.asyncio
    async def test_skips_retry_on_4xx(self):
        """ClarinetAPIError with 4xx status code should not be retried."""
        from taskiq import TaskiqMessage, TaskiqResult

        from clarinet.client import ClarinetAPIError
        from clarinet.services.pipeline.middleware import RetryMiddleware

        middleware = RetryMiddleware(default_retry_count=3, default_retry_label=True)
        msg = TaskiqMessage(task_id="t1", task_name="test_task", labels={}, args=[], kwargs={})
        exc = ClarinetAPIError("Conflict", status_code=409, detail="Invalid submit status")
        result = TaskiqResult(is_err=True, return_value=None, execution_time=0.1, error=exc)

        with patch(
            "taskiq.middlewares.SmartRetryMiddleware.on_error", new_callable=AsyncMock
        ) as mock_super:
            await middleware.on_error(msg, result, exc)
            mock_super.assert_not_called()

    @pytest.mark.asyncio
    async def test_retries_on_5xx(self):
        """ClarinetAPIError with 5xx status code should be retried normally."""
        from taskiq import TaskiqMessage, TaskiqResult

        from clarinet.client import ClarinetAPIError
        from clarinet.services.pipeline.middleware import RetryMiddleware

        middleware = RetryMiddleware(default_retry_count=3, default_retry_label=True)
        msg = TaskiqMessage(task_id="t2", task_name="test_task", labels={}, args=[], kwargs={})
        exc = ClarinetAPIError("Internal error", status_code=502)
        result = TaskiqResult(is_err=True, return_value=None, execution_time=0.1, error=exc)

        with patch(
            "taskiq.middlewares.SmartRetryMiddleware.on_error", new_callable=AsyncMock
        ) as mock_super:
            await middleware.on_error(msg, result, exc)
            mock_super.assert_called_once_with(msg, result, exc)

    @pytest.mark.asyncio
    async def test_retries_on_non_api_error(self):
        """Non-ClarinetAPIError exceptions should be retried normally."""
        from taskiq import TaskiqMessage, TaskiqResult

        from clarinet.services.pipeline.middleware import RetryMiddleware

        middleware = RetryMiddleware(default_retry_count=3, default_retry_label=True)
        msg = TaskiqMessage(task_id="t3", task_name="test_task", labels={}, args=[], kwargs={})
        exc = ConnectionError("RabbitMQ connection lost")
        result = TaskiqResult(is_err=True, return_value=None, execution_time=0.1, error=exc)

        with patch(
            "taskiq.middlewares.SmartRetryMiddleware.on_error", new_callable=AsyncMock
        ) as mock_super:
            await middleware.on_error(msg, result, exc)
            mock_super.assert_called_once_with(msg, result, exc)

    @pytest.mark.asyncio
    async def test_retries_on_api_error_without_status_code(self):
        """ClarinetAPIError without status_code should be retried."""
        from taskiq import TaskiqMessage, TaskiqResult

        from clarinet.client import ClarinetAPIError
        from clarinet.services.pipeline.middleware import RetryMiddleware

        middleware = RetryMiddleware(default_retry_count=3, default_retry_label=True)
        msg = TaskiqMessage(task_id="t4", task_name="test_task", labels={}, args=[], kwargs={})
        exc = ClarinetAPIError("Unknown error")
        result = TaskiqResult(is_err=True, return_value=None, execution_time=0.1, error=exc)

        with patch(
            "taskiq.middlewares.SmartRetryMiddleware.on_error", new_callable=AsyncMock
        ) as mock_super:
            await middleware.on_error(msg, result, exc)
            mock_super.assert_called_once_with(msg, result, exc)


# ─── Worker signal handling (Windows regression) ─────────────────────────────


class TestWorkerSignalHandling:
    """Regression tests for signal handling in run_worker().

    On Windows, loop.add_signal_handler() raises NotImplementedError,
    causing the worker to exit immediately. Verify platform-appropriate
    signal registration is used.
    """

    @pytest.fixture()
    def _mock_worker_deps(self):
        """Mock all run_worker dependencies so only signal logic executes."""
        from taskiq import AsyncBroker

        mock_broker = MagicMock(spec=AsyncBroker)
        mock_broker.get_all_tasks.return_value = {}
        mock_broker.startup = AsyncMock()
        mock_broker.shutdown = AsyncMock()
        with (
            patch("clarinet.services.pipeline.worker.reconfigure_for_worker"),
            patch("clarinet.services.pipeline.worker.load_task_modules"),
            patch(
                "clarinet.services.pipeline.worker.get_worker_queues",
                return_value=[DEFAULT_QUEUE],
            ),
            patch(
                "clarinet.services.pipeline.broker.get_broker_for",
                return_value=mock_broker,
            ),
            patch("taskiq.api.receiver.run_receiver_task", new_callable=AsyncMock),
        ):
            yield

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_mock_worker_deps")
    async def test_unix_uses_loop_add_signal_handler(self):
        """On Unix, run_worker registers SIGINT and SIGTERM via loop.add_signal_handler."""
        from clarinet.services.pipeline.worker import run_worker

        with patch("sys.platform", "linux"), patch("signal.signal") as mock_signal:
            loop = asyncio.get_running_loop()

            registered_signals = []

            def spy_add_signal_handler(sig, callback):
                registered_signals.append(sig)
                # Set the event immediately so run_worker exits
                callback()

            with patch.object(loop, "add_signal_handler", side_effect=spy_add_signal_handler):
                await run_worker(queues=[DEFAULT_QUEUE])

            assert signal.SIGINT in registered_signals
            assert signal.SIGTERM in registered_signals
            mock_signal.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_mock_worker_deps")
    async def test_windows_uses_signal_signal(self):
        """On Windows, run_worker registers SIGINT via signal.signal() (not loop-based)."""
        from clarinet.services.pipeline.worker import run_worker

        with patch("sys.platform", "win32"):
            original_signal = signal.signal
            registered = {}

            def spy_signal(sig, handler):
                registered[sig] = handler
                # Trigger handler immediately so run_worker exits
                handler(sig, None)
                return original_signal

            with patch("signal.signal", side_effect=spy_signal):
                await run_worker(queues=[DEFAULT_QUEUE])

            assert signal.SIGINT in registered
            assert signal.SIGTERM not in registered

    @pytest.mark.asyncio
    @pytest.mark.usefixtures("_mock_worker_deps")
    async def test_windows_no_add_signal_handler_called(self):
        """On Windows, loop.add_signal_handler is never called (would raise NotImplementedError)."""
        from clarinet.services.pipeline.worker import run_worker

        with patch("sys.platform", "win32"):
            loop = asyncio.get_running_loop()

            def fail_add_signal_handler(*args, **kwargs):
                raise AssertionError("add_signal_handler must not be called on Windows")

            original_signal = signal.signal

            def trigger_shutdown(sig, handler):
                handler(sig, None)
                return original_signal

            with (
                patch.object(loop, "add_signal_handler", side_effect=fail_add_signal_handler),
                patch("signal.signal", side_effect=trigger_shutdown),
            ):
                await run_worker(queues=[DEFAULT_QUEUE])


# ─── Task registry collision detection ────────────────────────────────────────


class TestTaskRegistryCollision:
    """Tests for task name collision detection in register_task()."""

    def test_collision_raises_on_different_object(self):
        """Registering a task with same name but different object raises."""
        from clarinet.services.pipeline.chain import register_task

        task_a = AsyncMock()
        task_a.task_name = "collision_task"
        task_b = AsyncMock()
        task_b.task_name = "collision_task"

        register_task(task_a)
        with pytest.raises(PipelineConfigError, match="collision"):
            register_task(task_b)

    def test_idempotent_reregistration_of_same_object(self):
        """Re-registering the same task object does not raise."""
        from clarinet.services.pipeline.chain import register_task

        task = AsyncMock()
        task.task_name = "idempotent_task"

        register_task(task)
        register_task(task)  # must not raise
        assert _TASK_REGISTRY["idempotent_task"] is task

    def test_pipeline_step_collision_raises(self):
        """Pipeline.step() raises on collision via register_task."""
        task_a = AsyncMock()
        task_a.task_name = "step_collision"
        task_a._pipeline_queue = None
        task_b = AsyncMock()
        task_b.task_name = "step_collision"
        task_b._pipeline_queue = None

        Pipeline("pipeline_a").step(task_a)
        with pytest.raises(PipelineConfigError, match="collision"):
            Pipeline("pipeline_b").step(task_b)

    def test_pipeline_step_idempotent_same_task(self):
        """Same task in two pipelines — no error (idempotent)."""
        task = AsyncMock()
        task.task_name = "shared_task"
        task._pipeline_queue = None

        Pipeline("shared_a").step(task)
        Pipeline("shared_b").step(task)  # must not raise
        assert _TASK_REGISTRY["shared_task"] is task


# ─── Namespace in queue names ─────────────────────────────────────────────────


class TestQueueNamespacing:
    """Queue names are derived from settings.pipeline_task_namespace."""

    def test_default_namespace_keeps_clarinet_prefix(self, monkeypatch):
        """project_name='Clarinet' → queues stay 'clarinet.*' (backward compat)."""
        monkeypatch.setattr(settings, "project_name", "Clarinet")
        assert settings.default_queue_name == "clarinet.default"
        assert settings.gpu_queue_name == "clarinet.gpu"
        assert settings.dicom_queue_name == "clarinet.dicom"
        assert settings.dlq_queue_name == "clarinet.dead_letter"

    def test_custom_project_name_namespaces_all_queues(self, monkeypatch):
        """A custom project_name produces a namespaced queue set."""
        monkeypatch.setattr(settings, "project_name", "Liver Project")
        assert settings.default_queue_name == "liver_project.default"
        assert settings.gpu_queue_name == "liver_project.gpu"
        assert settings.dicom_queue_name == "liver_project.dicom"
        assert settings.dlq_queue_name == "liver_project.dead_letter"

    def test_decorator_binds_to_namespaced_queue(self, monkeypatch):
        """@pipeline_task() registers on the project-namespaced default queue."""
        from clarinet.services.pipeline import get_all_brokers, is_registered
        from clarinet.services.pipeline.task import pipeline_task

        monkeypatch.setattr(settings, "project_name", "Liver")
        reset_brokers()  # next get_broker_for(...) creates a fresh broker

        @pipeline_task()
        async def liver_default_task(msg, ctx):
            pass

        assert liver_default_task._pipeline_queue == "liver.default"
        assert is_registered("liver.default")
        assert "liver.default" in get_all_brokers()

    @pytest.mark.asyncio
    async def test_chain_dispatch_publishes_dlq_on_queue_mismatch(self):
        """When the persisted definition's queue disagrees with the registered
        task's bound queue, the chain middleware must skip dispatch and emit
        a chain_failure to the DLQ — guards against stale pipeline definitions
        rerouting tasks through the wrong broker."""
        from taskiq import TaskiqMessage

        from clarinet.services.pipeline.middleware import (
            DLQPublisher,
            PipelineChainMiddleware,
        )

        bound_task = AsyncMock()
        bound_task.task_name = "queue_mismatch_task"
        bound_task._pipeline_queue = GPU_QUEUE
        _TASK_REGISTRY[bound_task.task_name] = bound_task

        dlq = DLQPublisher()
        dlq.publish = AsyncMock()
        middleware = PipelineChainMiddleware(dlq=dlq)

        prev_message = TaskiqMessage(
            task_id="t-prev",
            task_name="prev",
            labels={"pipeline_id": "p", "step_index": "0"},
            args=[],
            kwargs={},
        )

        await middleware._dispatch_next_step(
            prev_message,
            "p",
            {"task_name": bound_task.task_name, "queue": DICOM_QUEUE},
            1,
            {"patient_id": "P", "study_uid": "S"},
        )

        bound_task.kicker.assert_not_called()
        dlq.publish.assert_called_once()
        payload = dlq.publish.call_args.args[0]
        assert payload["error_type"] == "chain_failure"
        assert bound_task.task_name in payload["error"]
        assert GPU_QUEUE in payload["error"]
        assert DICOM_QUEUE in payload["error"]
