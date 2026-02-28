"""Integration tests for Pipeline service with real RabbitMQ on klara.

Validates broker connectivity, task dispatch, queue routing, task execution,
multi-step chain advancement (DB-backed), and middleware logging against a live AMQP broker.

Auto-skipped when RabbitMQ on klara is unreachable.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import aio_pika
import pytest

from src.client import ClarinetClient
from src.repositories.pipeline_definition_repository import PipelineDefinitionRepository
from src.services.pipeline.chain import _TASK_REGISTRY
from src.services.pipeline.exceptions import PipelineStepError
from src.services.pipeline.message import PipelineMessage

pytestmark = [
    pytest.mark.pipeline,
    pytest.mark.asyncio,
    pytest.mark.usefixtures("_check_rabbitmq", "_purge_test_queues", "_clear_pipeline_registries"),
]


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _get_message_from_queue(
    rabbitmq_url: str,
    queue_name: str,
    wait_seconds: float = 5.0,
) -> aio_pika.abc.AbstractIncomingMessage | None:
    """Consume a single message from a queue via raw aio_pika, polling until available."""
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, passive=True)
        async with asyncio.timeout(wait_seconds):
            while True:
                msg = await queue.get(fail=False)
                if msg is not None:
                    return msg
                await asyncio.sleep(0.1)


async def _queue_message_count(
    rabbitmq_url: str,
    queue_name: str,
) -> int:
    """Return the number of messages currently in a queue."""
    connection = await aio_pika.connect_robust(rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, passive=True)
        return queue.declaration_result.message_count  # type: ignore[union-attr]


# ─── 1. Broker Connection ───────────────────────────────────────────────────


class TestBrokerConnection:
    """Basic RabbitMQ connectivity tests."""

    async def test_connect_and_shutdown(self, pipeline_broker_factory: Any) -> None:
        """AioPikaBroker startup/shutdown against klara succeeds."""
        broker = await pipeline_broker_factory("default")
        # Broker is started by factory — just verify no exception
        await broker.shutdown()

    async def test_exchange_created(
        self,
        pipeline_broker: Any,
        rabbitmq_url: str,
        test_exchange: str,
    ) -> None:
        """After startup, the test exchange exists (passive declare succeeds)."""
        connection = await aio_pika.connect_robust(rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            # passive=True raises ChannelNotFoundEntity if exchange doesn't exist
            exchange = await channel.declare_exchange(
                test_exchange, aio_pika.ExchangeType.DIRECT, passive=True
            )
            assert exchange.name == test_exchange

    async def test_queue_created(
        self,
        pipeline_broker: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """After startup, the default queue exists and is bound."""
        connection = await aio_pika.connect_robust(rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            queue = await channel.declare_queue(test_queues["default"], passive=True)
            assert queue.name == test_queues["default"]


# ─── 2. Task Dispatch ───────────────────────────────────────────────────────


class TestTaskDispatch:
    """Verify that dispatched tasks arrive in the RabbitMQ queue."""

    async def test_task_message_arrives_in_queue(
        self,
        pipeline_broker: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Register task, kiq it, consume via raw aio_pika — body contains args."""

        @pipeline_broker.task(task_name="test_echo")
        async def echo_task(data: dict[str, Any]) -> dict[str, Any]:
            return data

        payload = {"patient_id": "PAT001", "study_uid": "1.2.3"}
        await echo_task.kiq(payload)

        # Small delay for message to arrive
        await asyncio.sleep(0.3)

        msg = await _get_message_from_queue(rabbitmq_url, test_queues["default"])
        assert msg is not None
        body = json.loads(msg.body)
        # TaskIQ wraps args — the first positional arg should be our payload
        assert payload["patient_id"] in json.dumps(body)

    async def test_pipeline_message_survives_roundtrip(
        self,
        pipeline_broker: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Dispatch PipelineMessage, consume, deserialize — all fields preserved."""

        @pipeline_broker.task(task_name="test_roundtrip")
        async def roundtrip_task(data: dict[str, Any]) -> dict[str, Any]:
            return data

        original = PipelineMessage(
            patient_id="PAT002",
            study_uid="1.2.3.4",
            series_uid="1.2.3.4.5",
            record_id=42,
            payload={"threshold": 0.5},
            pipeline_id="test_pipe",
            step_index=1,
        )
        await roundtrip_task.kiq(original.model_dump())

        await asyncio.sleep(0.3)

        msg = await _get_message_from_queue(rabbitmq_url, test_queues["default"])
        assert msg is not None
        body = json.loads(msg.body)
        # Verify the PipelineMessage fields are in the serialized body
        body_str = json.dumps(body)
        assert "PAT002" in body_str
        assert "1.2.3.4.5" in body_str

    async def test_full_message_with_payload_roundtrip(
        self,
        pipeline_broker: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Full PipelineMessage with unicode payload survives serialization."""

        @pipeline_broker.task(task_name="test_unicode")
        async def unicode_task(data: dict[str, Any]) -> dict[str, Any]:
            return data

        original = PipelineMessage(
            patient_id="PAT003",
            study_uid="1.2.3",
            record_type_name="ct_scan",
            payload={"name": "Тест юникода", "emoji": "🧪", "nested": {"deep": True}},
        )
        await unicode_task.kiq(original.model_dump())

        await asyncio.sleep(0.3)

        msg = await _get_message_from_queue(rabbitmq_url, test_queues["default"])
        assert msg is not None
        body_str = json.dumps(json.loads(msg.body), ensure_ascii=False)
        assert "Тест юникода" in body_str

    async def test_labels_attached_to_message(
        self,
        pipeline_broker: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Custom labels (pipeline_id, step_index) appear in consumed message headers."""

        @pipeline_broker.task(task_name="test_labels")
        async def label_task(data: dict[str, Any]) -> dict[str, Any]:
            return data

        await (
            label_task.kicker()
            .with_labels(
                pipeline_id="my_pipeline",
                step_index="2",
            )
            .kiq({"patient_id": "P", "study_uid": "S"})
        )

        await asyncio.sleep(0.3)

        msg = await _get_message_from_queue(rabbitmq_url, test_queues["default"])
        assert msg is not None
        # TaskIQ embeds labels in the message body
        body = json.loads(msg.body)
        body_str = json.dumps(body)
        assert "my_pipeline" in body_str


# ─── 3. Queue Routing ───────────────────────────────────────────────────────


class TestQueueRouting:
    """Verify messages reach the correct queue based on broker routing."""

    async def test_default_queue_receives_its_messages(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Task on default broker lands in default queue, not gpu queue."""
        default_broker = await pipeline_broker_factory("default")
        gpu_broker = await pipeline_broker_factory("gpu")

        try:

            @default_broker.task(task_name="test_default_only")
            async def default_task(data: dict[str, Any]) -> dict[str, Any]:
                return data

            await default_task.kiq({"patient_id": "P", "study_uid": "S"})
            await asyncio.sleep(0.3)

            default_count = await _queue_message_count(rabbitmq_url, test_queues["default"])
            gpu_count = await _queue_message_count(rabbitmq_url, test_queues["gpu"])

            assert default_count >= 1
            assert gpu_count == 0
        finally:
            await default_broker.shutdown()
            await gpu_broker.shutdown()

    async def test_gpu_queue_receives_its_messages(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Task on gpu broker lands in gpu queue, not default queue."""
        default_broker = await pipeline_broker_factory("default")
        gpu_broker = await pipeline_broker_factory("gpu")

        try:

            @gpu_broker.task(task_name="test_gpu_only")
            async def gpu_task(data: dict[str, Any]) -> dict[str, Any]:
                return data

            await gpu_task.kiq({"patient_id": "P", "study_uid": "S"})
            await asyncio.sleep(0.3)

            default_count = await _queue_message_count(rabbitmq_url, test_queues["default"])
            gpu_count = await _queue_message_count(rabbitmq_url, test_queues["gpu"])

            assert gpu_count >= 1
            assert default_count == 0
        finally:
            await default_broker.shutdown()
            await gpu_broker.shutdown()

    async def test_queues_are_isolated(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Dispatch to both queues — each receives only its own task."""
        default_broker = await pipeline_broker_factory("default")
        gpu_broker = await pipeline_broker_factory("gpu")

        try:

            @default_broker.task(task_name="test_iso_default")
            async def default_task(data: dict[str, Any]) -> dict[str, Any]:
                return data

            @gpu_broker.task(task_name="test_iso_gpu")
            async def gpu_task(data: dict[str, Any]) -> dict[str, Any]:
                return data

            await default_task.kiq({"patient_id": "P1", "study_uid": "S1"})
            await gpu_task.kiq({"patient_id": "P2", "study_uid": "S2"})
            await asyncio.sleep(0.3)

            default_count = await _queue_message_count(rabbitmq_url, test_queues["default"])
            gpu_count = await _queue_message_count(rabbitmq_url, test_queues["gpu"])

            assert default_count == 1
            assert gpu_count == 1
        finally:
            await default_broker.shutdown()
            await gpu_broker.shutdown()


# ─── 4. Task Execution ──────────────────────────────────────────────────────


class TestTaskExecution:
    """Full send-receive-execute cycle using run_receiver_task."""

    async def test_task_executes_and_returns(
        self,
        pipeline_broker_factory: Any,
    ) -> None:
        """Task modifies dict and returns it. Receiver processes one message."""
        from taskiq.api import run_receiver_task

        broker = await pipeline_broker_factory("default", as_worker=True)
        try:
            results: list[dict[str, Any]] = []

            @broker.task(task_name="test_exec_return")
            async def exec_task(data: dict[str, Any]) -> dict[str, Any]:
                data["processed"] = True
                results.append(data)
                return data

            # Start receiver in background
            receiver = asyncio.create_task(run_receiver_task(broker))

            await exec_task.kiq({"patient_id": "P", "study_uid": "S"})
            await asyncio.sleep(1.0)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert len(results) == 1
            assert results[0]["processed"] is True
        finally:
            await broker.shutdown()

    async def test_task_side_effect_observed(
        self,
        pipeline_broker_factory: Any,
    ) -> None:
        """Task appends to shared list. After execution, list has expected items."""
        from taskiq.api import run_receiver_task

        broker = await pipeline_broker_factory("default", as_worker=True)
        try:
            side_effects: list[str] = []

            @broker.task(task_name="test_side_effect")
            async def side_effect_task(data: dict[str, Any]) -> dict[str, Any]:
                side_effects.append(f"processed:{data['patient_id']}")
                return data

            receiver = asyncio.create_task(run_receiver_task(broker))

            await side_effect_task.kiq({"patient_id": "PAT_A", "study_uid": "S"})
            await side_effect_task.kiq({"patient_id": "PAT_B", "study_uid": "S"})
            await asyncio.sleep(1.5)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert "processed:PAT_A" in side_effects
            assert "processed:PAT_B" in side_effects
        finally:
            await broker.shutdown()

    async def test_task_exception_captured(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Task raises PipelineStepError. Verify error is captured by receiver."""
        from taskiq.api import run_receiver_task

        broker = await pipeline_broker_factory("default", as_worker=True)
        try:
            error_captured: list[bool] = []

            @broker.task(task_name="test_exception")
            async def failing_task(data: dict[str, Any]) -> dict[str, Any]:
                error_captured.append(True)
                raise PipelineStepError("test_step", "Something went wrong")

            receiver = asyncio.create_task(run_receiver_task(broker))

            await failing_task.kiq({"patient_id": "P", "study_uid": "S"})
            await asyncio.sleep(1.0)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            # The task function was called (error was raised inside it)
            assert len(error_captured) == 1
        finally:
            await broker.shutdown()


# ─── 5. Pipeline Chain ──────────────────────────────────────────────────────


class TestPipelineChain:
    """Multi-step chain tests with real broker and chain middleware."""

    async def test_two_step_chain(
        self,
        pipeline_broker_factory: Any,
        test_queues: dict[str, str],
        test_session: Any,
        pipeline_clarinet_client: ClarinetClient,
    ) -> None:
        """Pipeline with 2 steps. Step 2 receives step 1's output."""
        from taskiq.api import run_receiver_task

        # Seed pipeline definition in DB
        repo = PipelineDefinitionRepository(test_session)
        await repo.upsert(
            "test_chain2",
            [
                {"task_name": "chain2_step1", "queue": test_queues["default"]},
                {"task_name": "chain2_step2", "queue": test_queues["default"]},
            ],
        )
        await test_session.commit()

        broker = await pipeline_broker_factory(
            "default",
            clarinet_client=pipeline_clarinet_client,
            with_middlewares=True,
            as_worker=True,
        )
        try:
            execution_log: list[str] = []
            done_event = asyncio.Event()

            @broker.task(task_name="chain2_step1")
            async def step1(data: dict[str, Any]) -> dict[str, Any]:
                execution_log.append("step1")
                msg = PipelineMessage(**data)
                msg.payload["step1_done"] = True
                return msg.model_dump()

            @broker.task(task_name="chain2_step2")
            async def step2(data: dict[str, Any]) -> dict[str, Any]:
                execution_log.append("step2")
                msg = PipelineMessage(**data)
                assert msg.payload.get("step1_done") is True
                msg.payload["step2_done"] = True
                done_event.set()
                return msg.model_dump()

            _TASK_REGISTRY["chain2_step1"] = step1
            _TASK_REGISTRY["chain2_step2"] = step2

            receiver = asyncio.create_task(run_receiver_task(broker))

            await (
                step1.kicker()
                .with_labels(
                    pipeline_id="test_chain2",
                    step_index="0",
                    routing_key="default",
                )
                .kiq(PipelineMessage(patient_id="P", study_uid="S").model_dump())
            )

            async with asyncio.timeout(10.0):
                await done_event.wait()

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert execution_log == ["step1", "step2"]
        finally:
            await broker.shutdown()

    async def test_three_step_payload_accumulation(
        self,
        pipeline_broker_factory: Any,
        test_queues: dict[str, str],
        test_session: Any,
        pipeline_clarinet_client: ClarinetClient,
    ) -> None:
        """3-step chain, each step adds key to payload. Final message has all 3 keys."""
        from taskiq.api import run_receiver_task

        repo = PipelineDefinitionRepository(test_session)
        await repo.upsert(
            "test_accum",
            [
                {"task_name": "accum_step1", "queue": test_queues["default"]},
                {"task_name": "accum_step2", "queue": test_queues["default"]},
                {"task_name": "accum_step3", "queue": test_queues["default"]},
            ],
        )
        await test_session.commit()

        broker = await pipeline_broker_factory(
            "default",
            clarinet_client=pipeline_clarinet_client,
            with_middlewares=True,
            as_worker=True,
        )
        try:
            final_payload: list[dict[str, Any]] = []
            done_event = asyncio.Event()

            @broker.task(task_name="accum_step1")
            async def step1(data: dict[str, Any]) -> dict[str, Any]:
                msg = PipelineMessage(**data)
                msg.payload["key1"] = "value1"
                return msg.model_dump()

            @broker.task(task_name="accum_step2")
            async def step2(data: dict[str, Any]) -> dict[str, Any]:
                msg = PipelineMessage(**data)
                msg.payload["key2"] = "value2"
                return msg.model_dump()

            @broker.task(task_name="accum_step3")
            async def step3(data: dict[str, Any]) -> dict[str, Any]:
                msg = PipelineMessage(**data)
                msg.payload["key3"] = "value3"
                final_payload.append(msg.payload.copy())
                done_event.set()
                return msg.model_dump()

            _TASK_REGISTRY["accum_step1"] = step1
            _TASK_REGISTRY["accum_step2"] = step2
            _TASK_REGISTRY["accum_step3"] = step3

            receiver = asyncio.create_task(run_receiver_task(broker))

            await (
                step1.kicker()
                .with_labels(
                    pipeline_id="test_accum",
                    step_index="0",
                    routing_key="default",
                )
                .kiq(PipelineMessage(patient_id="P", study_uid="S").model_dump())
            )

            async with asyncio.timeout(15.0):
                await done_event.wait()

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert len(final_payload) == 1
            assert final_payload[0]["key1"] == "value1"
            assert final_payload[0]["key2"] == "value2"
            assert final_payload[0]["key3"] == "value3"
        finally:
            await broker.shutdown()

    async def test_chain_stops_on_error(
        self,
        pipeline_broker_factory: Any,
        test_queues: dict[str, str],
        test_session: Any,
        pipeline_clarinet_client: ClarinetClient,
    ) -> None:
        """3-step chain, step 2 raises. Only steps 1 and 2 execute."""
        from taskiq.api import run_receiver_task

        repo = PipelineDefinitionRepository(test_session)
        await repo.upsert(
            "test_err_chain",
            [
                {"task_name": "err_step1", "queue": test_queues["default"]},
                {"task_name": "err_step2", "queue": test_queues["default"]},
                {"task_name": "err_step3", "queue": test_queues["default"]},
            ],
        )
        await test_session.commit()

        broker = await pipeline_broker_factory(
            "default",
            clarinet_client=pipeline_clarinet_client,
            with_middlewares=True,
            as_worker=True,
        )
        try:
            execution_log: list[str] = []
            step2_done = asyncio.Event()

            @broker.task(task_name="err_step1")
            async def step1(data: dict[str, Any]) -> dict[str, Any]:
                execution_log.append("step1")
                msg = PipelineMessage(**data)
                return msg.model_dump()

            @broker.task(task_name="err_step2")
            async def step2(data: dict[str, Any]) -> dict[str, Any]:
                execution_log.append("step2")
                step2_done.set()
                raise PipelineStepError("err_step2", "Intentional failure")

            @broker.task(task_name="err_step3")
            async def step3(data: dict[str, Any]) -> dict[str, Any]:
                execution_log.append("step3")
                return data

            _TASK_REGISTRY["err_step1"] = step1
            _TASK_REGISTRY["err_step2"] = step2
            _TASK_REGISTRY["err_step3"] = step3

            receiver = asyncio.create_task(run_receiver_task(broker))

            await (
                step1.kicker()
                .with_labels(
                    pipeline_id="test_err_chain",
                    step_index="0",
                    routing_key="default",
                )
                .kiq(PipelineMessage(patient_id="P", study_uid="S").model_dump())
            )

            # Wait until step 2 executes (and fails)
            async with asyncio.timeout(10.0):
                await step2_done.wait()
            # Extra wait to verify step 3 is NOT dispatched
            await asyncio.sleep(1.0)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert "step1" in execution_log
            assert "step2" in execution_log
            assert "step3" not in execution_log
        finally:
            await broker.shutdown()


# ─── 6. Chain Failure Observability ─────────────────────────────────────────


class TestPipelineChainNegative:
    """Verify that chain advancement failures are routed to DLQ and logged.

    Each test covers one silent-failure branch in PipelineChainMiddleware and
    checks three things:
      1. The next step is NOT executed.
      2. A ``chain_failure`` record arrives in the DLQ.
      3. An ERROR log containing the failure context is emitted.
    """

    async def test_chain_failure_when_pipeline_not_found(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
        test_session: Any,
        pipeline_clarinet_client: ClarinetClient,
        capture_logs: list[str],
    ) -> None:
        """Step completes OK, but pipeline_id does not exist in DB → API 404 → DLQ."""
        from taskiq.api import run_receiver_task

        # NOTE: "ghost_chain" is intentionally NOT seeded in the DB.
        dlq_queue_name = test_queues["dlq"]
        step2_executed: list[bool] = [False]
        step1_done = asyncio.Event()

        broker = await pipeline_broker_factory(
            "default",
            clarinet_client=pipeline_clarinet_client,
            with_middlewares=True,
            as_worker=True,
        )
        try:

            @broker.task(task_name="ghost_step1")
            async def step1(data: dict[str, Any]) -> dict[str, Any]:
                step1_done.set()
                return data

            @broker.task(task_name="ghost_step2")
            async def step2(data: dict[str, Any]) -> dict[str, Any]:
                step2_executed[0] = True
                return data

            _TASK_REGISTRY["ghost_step1"] = step1
            _TASK_REGISTRY["ghost_step2"] = step2

            receiver = asyncio.create_task(run_receiver_task(broker))

            with patch("src.services.pipeline.broker.DLQ_QUEUE", dlq_queue_name):
                await (
                    step1.kicker()
                    .with_labels(
                        pipeline_id="ghost_chain",
                        step_index="0",
                        routing_key="default",
                    )
                    .kiq(PipelineMessage(patient_id="P", study_uid="S").model_dump())
                )

                async with asyncio.timeout(10.0):
                    await step1_done.wait()
                # Allow time for chain-failure DLQ publish
                await asyncio.sleep(1.5)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            # step2 must NOT have been dispatched
            assert not step2_executed[0], "step2 should not execute when pipeline not found"

            # DLQ must contain a chain_failure record for "ghost_chain"
            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                dlq = await channel.declare_queue(dlq_queue_name, durable=True)
                msg = await dlq.get(fail=False, no_ack=True)
                assert msg is not None, "No message found in DLQ after chain failure"
                body = json.loads(msg.body)

            assert body["error_type"] == "chain_failure"
            assert body["labels"]["pipeline_id"] == "ghost_chain"
            assert "not found" in body["error"].lower() or "404" in body["error"]

            # Log must contain the pipeline id and failure context
            assert any("ghost_chain" in m for m in capture_logs)
            assert any("not found" in m.lower() or "404" in m for m in capture_logs)
        finally:
            await broker.shutdown()

    async def test_chain_failure_when_task_not_in_registry(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
        test_session: Any,
        pipeline_clarinet_client: ClarinetClient,
        capture_logs: list[str],
    ) -> None:
        """Next step's task is absent from _TASK_REGISTRY → chain_failure → DLQ."""
        from taskiq.api import run_receiver_task

        dlq_queue_name = test_queues["dlq"]
        repo = PipelineDefinitionRepository(test_session)
        await repo.upsert(
            "missing_task_chain",
            [
                {"task_name": "notfound_step1", "queue": test_queues["default"]},
                # "notfound_step2_missing" is intentionally NOT registered below
                {"task_name": "notfound_step2_missing", "queue": test_queues["default"]},
            ],
        )
        await test_session.commit()

        step1_done = asyncio.Event()
        step2_executed: list[bool] = [False]

        broker = await pipeline_broker_factory(
            "default",
            clarinet_client=pipeline_clarinet_client,
            with_middlewares=True,
            as_worker=True,
        )
        try:

            @broker.task(task_name="notfound_step1")
            async def step1(data: dict[str, Any]) -> dict[str, Any]:
                step1_done.set()
                return data

            # Deliberately omit: _TASK_REGISTRY["notfound_step2_missing"] = ...
            _TASK_REGISTRY["notfound_step1"] = step1

            receiver = asyncio.create_task(run_receiver_task(broker))

            with patch("src.services.pipeline.broker.DLQ_QUEUE", dlq_queue_name):
                await (
                    step1.kicker()
                    .with_labels(
                        pipeline_id="missing_task_chain",
                        step_index="0",
                        routing_key="default",
                    )
                    .kiq(PipelineMessage(patient_id="P", study_uid="S").model_dump())
                )

                async with asyncio.timeout(10.0):
                    await step1_done.wait()
                await asyncio.sleep(1.5)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert not step2_executed[0]

            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                dlq = await channel.declare_queue(dlq_queue_name, durable=True)
                msg = await dlq.get(fail=False, no_ack=True)
                assert msg is not None, "No chain_failure message in DLQ"
                body = json.loads(msg.body)

            assert body["error_type"] == "chain_failure"
            assert body["labels"]["pipeline_id"] == "missing_task_chain"
            assert "notfound_step2_missing" in body["error"]
            assert "not in registry" in body["error"].lower() or "registry" in body["error"].lower()

            assert any("notfound_step2_missing" in m for m in capture_logs)
            assert any("registry" in m.lower() for m in capture_logs)
        finally:
            await broker.shutdown()

    async def test_chain_failure_when_unexpected_result_type(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
        test_session: Any,
        pipeline_clarinet_client: ClarinetClient,
        capture_logs: list[str],
    ) -> None:
        """Step returns a str instead of dict/PipelineMessage → chain_failure → DLQ."""
        from taskiq.api import run_receiver_task

        dlq_queue_name = test_queues["dlq"]
        repo = PipelineDefinitionRepository(test_session)
        await repo.upsert(
            "bad_result_chain",
            [
                {"task_name": "bad_result_step1", "queue": test_queues["default"]},
                {"task_name": "bad_result_step2", "queue": test_queues["default"]},
            ],
        )
        await test_session.commit()

        step1_done = asyncio.Event()
        step2_executed: list[bool] = [False]

        broker = await pipeline_broker_factory(
            "default",
            clarinet_client=pipeline_clarinet_client,
            with_middlewares=True,
            as_worker=True,
        )
        try:

            @broker.task(task_name="bad_result_step1")
            async def step1(data: dict[str, Any]) -> str:  # type: ignore[override]
                step1_done.set()
                return "not a dict or PipelineMessage"

            @broker.task(task_name="bad_result_step2")
            async def step2(data: dict[str, Any]) -> dict[str, Any]:
                step2_executed[0] = True
                return data

            _TASK_REGISTRY["bad_result_step1"] = step1
            _TASK_REGISTRY["bad_result_step2"] = step2

            receiver = asyncio.create_task(run_receiver_task(broker))

            with patch("src.services.pipeline.broker.DLQ_QUEUE", dlq_queue_name):
                await (
                    step1.kicker()
                    .with_labels(
                        pipeline_id="bad_result_chain",
                        step_index="0",
                        routing_key="default",
                    )
                    .kiq(PipelineMessage(patient_id="P", study_uid="S").model_dump())
                )

                async with asyncio.timeout(10.0):
                    await step1_done.wait()
                await asyncio.sleep(1.5)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert not step2_executed[0], "step2 must not run when step1 returns wrong type"

            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                dlq = await channel.declare_queue(dlq_queue_name, durable=True)
                msg = await dlq.get(fail=False, no_ack=True)
                assert msg is not None, "No chain_failure message in DLQ"
                body = json.loads(msg.body)

            assert body["error_type"] == "chain_failure"
            assert body["labels"]["pipeline_id"] == "bad_result_chain"
            assert "unexpected result type" in body["error"].lower()
            assert "str" in body["error"]

            assert any("unexpected result type" in m.lower() for m in capture_logs)
            assert any("str" in m for m in capture_logs)
        finally:
            await broker.shutdown()


# ─── 6. Middleware Logging ───────────────────────────────────────────────────


class TestDeadLetterQueue:
    """Verify failed tasks are routed to the dead letter queue after retries."""

    async def test_failed_task_arrives_in_dlq(
        self,
        pipeline_broker_factory: Any,
        rabbitmq_url: str,
        test_queues: dict[str, str],
    ) -> None:
        """Task that always raises is retried then routed to DLQ."""
        from taskiq.api import run_receiver_task

        broker = await pipeline_broker_factory("default", with_middlewares=True, as_worker=True)
        dlq_queue_name = test_queues["dlq"]

        # Pre-declare the DLQ queue so DeadLetterMiddleware writes to it
        # (override DLQ_QUEUE to use the test-isolated name)
        try:
            call_count: list[int] = [0]

            @broker.task(task_name="test_dlq_fail")
            async def always_failing_task(data: dict[str, Any]) -> dict[str, Any]:
                call_count[0] += 1
                raise PipelineStepError("test_dlq_fail", "Intentional failure for DLQ test")

            receiver = asyncio.create_task(run_receiver_task(broker))

            # Patch DLQ_QUEUE to use the test-isolated queue name
            with patch("src.services.pipeline.broker.DLQ_QUEUE", dlq_queue_name):
                await always_failing_task.kiq({"patient_id": "P", "study_uid": "S"})

                # Wait for retries (3 retries with 1s delay) + DLQ publish
                await asyncio.sleep(8.0)

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            # Verify: the task was called multiple times (initial + retries)
            assert call_count[0] >= 2, f"Expected multiple calls, got {call_count[0]}"

            # Verify: message arrived in DLQ
            connection = await aio_pika.connect_robust(rabbitmq_url)
            async with connection:
                channel = await connection.channel()
                dlq = await channel.declare_queue(dlq_queue_name, durable=True)
                msg = await dlq.get(fail=False, no_ack=True)
                assert msg is not None, "No message found in DLQ"

                dlq_body = json.loads(msg.body)
                assert dlq_body["task_name"] == "test_dlq_fail"
                assert "Intentional failure" in dlq_body["error"]
                assert dlq_body["error_type"] == "PipelineStepError"
        finally:
            await broker.shutdown()


# ─── 6. Middleware Logging ───────────────────────────────────────────────────


class TestMiddlewareLogging:
    """Verify logging middleware produces expected log output."""

    async def test_pre_send_logs_dispatch(
        self,
        pipeline_broker_factory: Any,
    ) -> None:
        """Dispatch task with pipeline labels — middleware pre_send is called."""
        from src.services.pipeline.middleware import PipelineLoggingMiddleware

        broker = await pipeline_broker_factory("default", with_middlewares=True)
        try:
            pre_send_calls: list[str] = []
            original_pre_send = PipelineLoggingMiddleware.pre_send

            async def capturing_pre_send(
                self: Any,
                message: Any,
            ) -> Any:
                pre_send_calls.append(message.task_name)
                return await original_pre_send(self, message)

            @broker.task(task_name="test_log_send")
            async def log_task(data: dict[str, Any]) -> dict[str, Any]:
                return data

            with patch.object(PipelineLoggingMiddleware, "pre_send", capturing_pre_send):
                await (
                    log_task.kicker()
                    .with_labels(
                        pipeline_id="log_test",
                        step_index="0",
                    )
                    .kiq({"patient_id": "P", "study_uid": "S"})
                )

            assert "test_log_send" in pre_send_calls
        finally:
            await broker.shutdown()

    async def test_post_execute_logs_completion(
        self,
        pipeline_broker_factory: Any,
    ) -> None:
        """Execute task via receiver — middleware post_execute is called."""
        from taskiq.api import run_receiver_task

        from src.services.pipeline.middleware import PipelineLoggingMiddleware

        broker = await pipeline_broker_factory("default", with_middlewares=True, as_worker=True)
        try:
            post_execute_calls: list[str] = []
            original_post_execute = PipelineLoggingMiddleware.post_execute

            async def capturing_post_execute(
                self: Any,
                message: Any,
                result: Any,
            ) -> None:
                post_execute_calls.append(message.task_name)
                await original_post_execute(self, message, result)

            @broker.task(task_name="test_log_exec")
            async def exec_task(data: dict[str, Any]) -> dict[str, Any]:
                return data

            done_event = asyncio.Event()
            original_capturing = capturing_post_execute

            async def capturing_with_event(
                self: Any,
                message: Any,
                result: Any,
            ) -> None:
                await original_capturing(self, message, result)
                done_event.set()

            with patch.object(PipelineLoggingMiddleware, "post_execute", capturing_with_event):
                receiver = asyncio.create_task(run_receiver_task(broker))
                await exec_task.kiq({"patient_id": "P", "study_uid": "S"})

                async with asyncio.timeout(10.0):
                    await done_event.wait()

            receiver.cancel()
            with pytest.raises(asyncio.CancelledError):
                await receiver

            assert "test_log_exec" in post_execute_calls
        finally:
            await broker.shutdown()
