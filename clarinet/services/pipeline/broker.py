"""
TaskIQ broker configuration for the pipeline service.

Provides a per-queue ``AioPikaBroker`` registry with ``RetryMiddleware``
and a project-namespaced dead letter queue.  Each queue gets its own broker
instance so that ``task.kicker().kiq()`` always routes to the queue the task
was registered for, even when a worker handles multiple queues.

Queue names are derived from ``settings.pipeline_task_namespace`` (which in
turn comes from ``settings.project_name``).  For the default ``project_name
= "Clarinet"`` the queues are ``clarinet.default``, ``clarinet.gpu``,
``clarinet.dicom``, ``clarinet.dead_letter`` — preserving backward
compatibility.  Other projects get isolated queues like
``liver.default``/``liver.gpu``/...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clarinet.settings import settings
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from taskiq import AsyncBroker
    from taskiq.abc.result_backend import AsyncResultBackend

# Per-queue broker registry. Populated lazily by ``get_broker_for``.
_BROKERS: dict[str, AsyncBroker] = {}


def _build_amqp_url() -> str:
    """Build AMQP connection URL from settings."""
    from urllib.parse import quote

    login = quote(settings.rabbitmq_login, safe="")
    password = quote(settings.rabbitmq_password, safe="")
    return f"amqp://{login}:{password}@{settings.rabbitmq_host}:{settings.rabbitmq_port}/"


def create_broker(queue_name: str) -> AsyncBroker:
    """Create a TaskIQ broker bound to a single queue.

    The routing key equals the full queue name — this guarantees that
    publishes from one project broker never land in another project's
    queue, even when multiple Clarinet deployments share the same
    RabbitMQ exchange.

    Args:
        queue_name: Full queue name (e.g. ``settings.gpu_queue_name``).

    Returns:
        Configured AioPikaBroker instance.
    """
    from aio_pika import ExchangeType
    from taskiq_aio_pika import AioPikaBroker
    from taskiq_aio_pika.exchange import Exchange
    from taskiq_aio_pika.queue import Queue as RmqQueue
    from taskiq_aio_pika.queue import QueueType

    routing_key = queue_name

    broker = AioPikaBroker(
        url=_build_amqp_url(),
        dead_letter_queue=RmqQueue(
            name=settings.dlq_queue_name,
            declare=True,
            durable=True,
            type=QueueType.CLASSIC,
        ),
        exchange=Exchange(
            name=settings.rabbitmq_exchange,
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

    from .middleware import (
        DeadLetterMiddleware,
        DLQPublisher,
        PipelineChainMiddleware,
        PipelineLoggingMiddleware,
        RetryMiddleware,
    )

    dlq = DLQPublisher()
    broker = broker.with_middlewares(
        RetryMiddleware(
            default_retry_count=settings.pipeline_retry_count,
            default_retry_label=True,
            default_delay=settings.pipeline_retry_delay,
            use_jitter=True,
            use_delay_exponent=True,
            max_delay_exponent=settings.pipeline_retry_max_delay,
        ),
        PipelineLoggingMiddleware(),
        DeadLetterMiddleware(dlq),
        PipelineChainMiddleware(dlq),
    )

    if settings.pipeline_result_backend_url:
        try:
            from taskiq_redis import RedisAsyncResultBackend

            backend: AsyncResultBackend[Any] = RedisAsyncResultBackend(
                settings.pipeline_result_backend_url
            )
            broker = broker.with_result_backend(backend)
            logger.debug("Pipeline result backend configured: Redis")
        except ImportError:
            logger.warning(
                "taskiq-redis not installed; pipeline result backend disabled. "
                "Install with: uv add taskiq-redis"
            )

    logger.debug(f"Created pipeline broker for queue '{queue_name}'")
    return broker


def get_broker_for(queue_name: str) -> AsyncBroker:
    """Return the cached broker for *queue_name*, creating it on first use.

    Tasks registered for different queues end up on different broker
    instances — so ``task.kicker().kiq()`` always publishes to the queue
    that owns the task, regardless of which worker calls it.

    Not thread-safe: assumes initialization happens from a single thread
    (decorators import-time, lifespan startup).  If concurrent callers
    are ever added, wrap the check-and-insert with a lock.
    """
    if queue_name not in _BROKERS:
        _BROKERS[queue_name] = create_broker(queue_name)
    return _BROKERS[queue_name]


def is_registered(queue_name: str) -> bool:
    """Return True when a broker for *queue_name* has been created."""
    return queue_name in _BROKERS


def get_broker() -> AsyncBroker:
    """Return the broker for the project's default queue.

    Backward-compat shim for callers that did not pick a queue explicitly.
    """
    return get_broker_for(settings.default_queue_name)


def get_all_brokers() -> dict[str, AsyncBroker]:
    """Return a snapshot of all brokers created so far."""
    return dict(_BROKERS)


def reset_brokers() -> None:
    """Drop the broker registry without shutting any broker down.

    Caller contract: any broker previously returned by ``get_broker_for``
    must already have been shut down (``await broker.shutdown()``) before
    calling this — otherwise the open AMQP connection leaks.  Intended
    for the API lifespan teardown (which awaits shutdown for each
    broker first) and for tests that re-build the registry between cases.
    """
    _BROKERS.clear()


def get_test_broker() -> AsyncBroker:
    """Create an InMemoryBroker for testing."""
    from taskiq import InMemoryBroker

    return InMemoryBroker()
