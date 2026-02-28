"""Fixtures for integration tests requiring external services (Slicer, RabbitMQ)."""

from __future__ import annotations

import socket
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from loguru import logger

from src.client import ClarinetClient
from src.services.slicer.client import SlicerClient
from src.services.slicer.service import SlicerService

# ─── Pipeline / RabbitMQ fixtures ────────────────────────────────────────────

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
    return f"clarinet_test_{test_run_id}"


@pytest.fixture(scope="session")
def test_queues(test_run_id: str) -> dict[str, str]:
    """Unique queue names for this test run."""
    return {
        "default": f"test_default_{test_run_id}",
        "gpu": f"test_gpu_{test_run_id}",
        "dicom": f"test_dicom_{test_run_id}",
        "dlq": f"test_dlq_{test_run_id}",
    }


@pytest_asyncio.fixture
async def pipeline_clarinet_client(
    test_session: Any, test_settings: Any
) -> AsyncGenerator[ClarinetClient]:
    """ClarinetClient backed by ASGI transport for pipeline chain tests.

    Shares the test database session so pipeline definitions seeded in tests
    are visible to the client via the FastAPI app.
    """
    from httpx import ASGITransport, AsyncClient

    from src.api.app import app
    from src.utils.database import get_async_session

    async def override_get_session() -> AsyncGenerator:
        yield test_session

    app.dependency_overrides[get_async_session] = override_get_session

    http_client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test/api",
        cookies={},
    )

    clarinet = ClarinetClient(base_url="http://test/api", auto_login=False)
    clarinet.client = http_client

    yield clarinet

    await clarinet.close()
    app.dependency_overrides.pop(get_async_session, None)


@pytest.fixture
def pipeline_broker_factory(
    rabbitmq_url: str,
    test_exchange: str,
    test_queues: dict[str, str],
) -> Any:
    """Factory that creates AioPikaBroker instances for a given queue key.

    Usage::

        broker = await pipeline_broker_factory("default")
        broker = await pipeline_broker_factory("gpu", with_middlewares=True)
        broker = await pipeline_broker_factory(
            "default", clarinet_client=client, with_middlewares=True
        )
    """
    from taskiq.middlewares import SmartRetryMiddleware
    from taskiq_aio_pika import AioPikaBroker

    from src.services.pipeline.middleware import (
        DeadLetterMiddleware,
        PipelineChainMiddleware,
        PipelineLoggingMiddleware,
    )

    async def _create(
        queue_key: str = "default",
        *,
        clarinet_client: ClarinetClient | None = None,
        with_middlewares: bool = False,
        as_worker: bool = False,
    ) -> AioPikaBroker:
        queue_name = test_queues[queue_key]
        routing_key = queue_key

        broker = AioPikaBroker(
            url=rabbitmq_url,
            exchange_name=test_exchange,
            exchange_type="direct",
            queue_name=queue_name,
            routing_key=routing_key,
            declare_exchange=True,
            declare_queues=True,
        )

        if with_middlewares:
            middlewares = [
                SmartRetryMiddleware(
                    default_retry_count=3,
                    default_retry_label=True,
                    default_delay=1,
                    use_jitter=False,
                    use_delay_exponent=False,
                ),
                PipelineLoggingMiddleware(),
                DeadLetterMiddleware(amqp_url=rabbitmq_url),
            ]
            if clarinet_client is not None:
                middlewares.append(
                    PipelineChainMiddleware(client=clarinet_client, amqp_url=rabbitmq_url)
                )
            broker = broker.with_middlewares(*middlewares)

        if as_worker:
            broker.is_worker_process = True

        await broker.startup()
        return broker

    return _create


@pytest_asyncio.fixture
async def pipeline_broker(
    pipeline_broker_factory: Any,
) -> AsyncGenerator[Any]:
    """A default pipeline broker that is started and cleaned up automatically."""
    broker = await pipeline_broker_factory("default")
    yield broker
    await broker.shutdown()


@pytest.fixture
def capture_logs() -> Generator[list[str]]:
    """Capture loguru ERROR (and above) log messages during a test.

    Yields a list that is populated with ``record["message"]`` strings as
    the test runs.  The loguru sink is removed automatically after the test.

    Usage::

        def test_something(capture_logs):
            do_thing()
            assert any("expected phrase" in m for m in capture_logs)
    """
    messages: list[str] = []

    def _sink(message: Any) -> None:
        messages.append(message.record["message"])

    sink_id = logger.add(_sink, level="ERROR", format="{message}")
    yield messages
    logger.remove(sink_id)


@pytest_asyncio.fixture(autouse=False)
async def _purge_test_queues(
    request: pytest.FixtureRequest,
    rabbitmq_url: str,
    test_queues: dict[str, str],
) -> AsyncGenerator[None]:
    """Purge all test queues before and after each pipeline-marked test."""
    if "pipeline" not in {m.name for m in request.node.iter_markers()}:
        yield
        return

    import aio_pika

    async def _purge() -> None:
        connection = await aio_pika.connect_robust(rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            for queue_name in test_queues.values():
                try:
                    queue = await channel.declare_queue(queue_name, passive=True)
                    await queue.purge()
                except Exception:
                    pass

    await _purge()
    yield
    await _purge()


@pytest_asyncio.fixture(scope="session", autouse=False)
async def _delete_test_resources(
    rabbitmq_url: str,
    test_exchange: str,
    test_queues: dict[str, str],
) -> AsyncGenerator[None]:
    """Session finalizer: delete test queues and exchange from RabbitMQ."""
    yield

    import aio_pika

    try:
        connection = await aio_pika.connect_robust(rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            for queue_name in test_queues.values():
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


@pytest.fixture(autouse=False)
def _clear_pipeline_registries() -> Any:
    """Clear pipeline task and pipeline registries before/after each test."""
    from src.services.pipeline.chain import _PIPELINE_REGISTRY, _TASK_REGISTRY

    _TASK_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()
    yield
    _TASK_REGISTRY.clear()
    _PIPELINE_REGISTRY.clear()


@pytest.fixture
def slicer_url() -> str:
    """Base URL for the local Slicer web server."""
    return "http://localhost:2016"


@pytest.fixture
def slicer_service() -> SlicerService:
    """SlicerService instance with cached helper source."""
    return SlicerService()


@pytest_asyncio.fixture
async def slicer_client(slicer_url: str) -> SlicerClient:
    """Async SlicerClient connected to the local Slicer instance."""
    async with SlicerClient(slicer_url) as client:
        yield client


@pytest.fixture
def test_images_path() -> Path:
    """Path to test images directory.

    Place test NRRD/NIfTI files here for Slicer integration tests.
    """
    return Path(__file__).parent / "test_data" / "slicer"
