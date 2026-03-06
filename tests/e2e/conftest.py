"""E2E test configuration — uses unauthenticated client for auth workflow tests."""

import socket
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient


@pytest_asyncio.fixture
async def client(unauthenticated_client) -> AsyncGenerator[AsyncClient]:
    """Override client with unauthenticated version for e2e auth tests."""
    yield unauthenticated_client


# ─── Pipeline / RabbitMQ fixtures (shared with integration tests) ─────────────

RABBITMQ_HOST = "192.168.122.151"  # klara VM
RABBITMQ_PORT = 5672


@pytest.fixture(scope="session")
def rabbitmq_url() -> str:
    """AMQP connection URL for RabbitMQ on klara."""
    return f"amqp://clarinet_test:clarinet_test@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"


@pytest.fixture(scope="session")
def _check_rabbitmq() -> None:
    """Skip all pipeline tests if RabbitMQ on klara is unreachable."""
    try:
        sock = socket.create_connection((RABBITMQ_HOST, RABBITMQ_PORT), timeout=3)
        sock.close()
    except OSError:
        pytest.skip(f"RabbitMQ not reachable at {RABBITMQ_HOST}:{RABBITMQ_PORT}")


@pytest.fixture(scope="session")
def test_run_id() -> str:
    """Unique run ID for test isolation."""
    return uuid4().hex[:8]


@pytest.fixture(scope="session")
def test_exchange(test_run_id: str) -> str:
    """Unique exchange name for this test run."""
    return f"clarinet_e2e_{test_run_id}"


@pytest.fixture(scope="session")
def test_queues(test_run_id: str) -> dict[str, str]:
    """Unique queue names for this test run."""
    return {
        "default": f"e2e_default_{test_run_id}",
        "gpu": f"e2e_gpu_{test_run_id}",
        "dicom": f"e2e_dicom_{test_run_id}",
        "dlq": f"e2e_dlq_{test_run_id}",
    }


@pytest.fixture
def pipeline_broker_factory(
    rabbitmq_url: str,
    test_exchange: str,
    test_queues: dict[str, str],
) -> Any:
    """Factory that creates AioPikaBroker instances for a given queue key."""
    from aio_pika import ExchangeType
    from taskiq_aio_pika import AioPikaBroker
    from taskiq_aio_pika.exchange import Exchange
    from taskiq_aio_pika.queue import Queue as RmqQueue
    from taskiq_aio_pika.queue import QueueType

    async def _create(
        queue_key: str = "default",
        *,
        as_worker: bool = False,
    ) -> AioPikaBroker:
        queue_name = test_queues[queue_key]
        routing_key = queue_key

        broker = AioPikaBroker(
            url=rabbitmq_url,
            exchange=Exchange(
                name=test_exchange,
                type=ExchangeType.DIRECT,
                declare=True,
            ),
            task_queues=[
                RmqQueue(
                    name=queue_name,
                    routing_key=routing_key,
                    declare=True,
                    durable=True,
                    type=QueueType.CLASSIC,
                ),
            ],
            delay_queue=RmqQueue(
                name=f"{queue_name}.delay",
                declare=True,
                durable=True,
                type=QueueType.CLASSIC,
            ),
        )

        if as_worker:
            broker.is_worker_process = True

        await broker.startup()
        return broker

    return _create


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _delete_e2e_test_resources(
    rabbitmq_url: str,
    test_exchange: str,
    test_queues: dict[str, str],
) -> AsyncGenerator[None]:
    """Session finalizer: delete e2e test queues and exchange from RabbitMQ."""
    yield

    import asyncio

    import aio_pika

    try:
        connection = await asyncio.wait_for(aio_pika.connect(rabbitmq_url), timeout=5)
        async with connection:
            channel = await connection.channel()
            all_queue_names = list(test_queues.values())
            for name in list(all_queue_names):
                all_queue_names.append(f"{name}.delay")
            for queue_name in all_queue_names:
                try:
                    queue = await channel.declare_queue(queue_name, passive=True)
                    await queue.delete()
                except Exception:
                    pass
            try:
                exchange = await channel.declare_exchange(
                    test_exchange, aio_pika.ExchangeType.DIRECT, passive=True
                )
                await exchange.delete()
            except Exception:
                pass
    except Exception:
        pass
