"""
TaskIQ broker configuration for the pipeline service.

Provides AioPikaBroker singleton with SmartRetryMiddleware and dead letter queue.
Uses existing RabbitMQ settings from clarinet.settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from clarinet.settings import settings
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from taskiq import AsyncBroker
    from taskiq.abc.result_backend import AsyncResultBackend

# Module-level broker reference, initialized lazily
_broker: AsyncBroker | None = None

# Dead letter queue name
DLQ_QUEUE = "clarinet.dead_letter"

# Default queues
DEFAULT_QUEUE = "clarinet.default"
GPU_QUEUE = "clarinet.gpu"
DICOM_QUEUE = "clarinet.dicom"


def _build_amqp_url() -> str:
    """Build AMQP connection URL from settings.

    Returns:
        AMQP URL string.
    """
    return (
        f"amqp://{settings.rabbitmq_login}:{settings.rabbitmq_password}"
        f"@{settings.rabbitmq_host}:{settings.rabbitmq_port}/"
    )


def extract_routing_key(queue_name: str) -> str:
    """Extract routing key from a queue name.

    Convention: ``clarinet.gpu`` → ``gpu``.

    Args:
        queue_name: Full queue name (e.g. ``clarinet.gpu``).

    Returns:
        The routing key suffix.
    """
    return queue_name.rsplit(".", maxsplit=1)[-1]


def create_broker(queue_name: str = DEFAULT_QUEUE) -> AsyncBroker:
    """Create a TaskIQ broker for a specific queue.

    All brokers share the same ``clarinet`` direct exchange.
    Each queue binds to a routing key matching its suffix
    (e.g. ``clarinet.gpu`` binds to routing key ``gpu``).

    Args:
        queue_name: Queue name to bind (default: ``clarinet.default``).

    Returns:
        Configured AioPikaBroker instance.
    """
    from aio_pika import ExchangeType
    from taskiq_aio_pika import AioPikaBroker
    from taskiq_aio_pika.exchange import Exchange
    from taskiq_aio_pika.queue import Queue as RmqQueue
    from taskiq_aio_pika.queue import QueueType

    routing_key = extract_routing_key(queue_name)

    broker = AioPikaBroker(
        url=_build_amqp_url(),
        dead_letter_queue=RmqQueue(
            name=DLQ_QUEUE,
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

    # Attach middlewares
    from taskiq.middlewares import SmartRetryMiddleware

    from .middleware import (
        DeadLetterMiddleware,
        DLQPublisher,
        PipelineChainMiddleware,
        PipelineLoggingMiddleware,
    )

    dlq = DLQPublisher()
    broker = broker.with_middlewares(
        SmartRetryMiddleware(
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

    # Attach result backend if configured
    if settings.pipeline_result_backend_url:
        try:
            from taskiq_redis import RedisAsyncResultBackend

            backend: AsyncResultBackend = RedisAsyncResultBackend(
                settings.pipeline_result_backend_url
            )
            broker = broker.with_result_backend(backend)
            logger.debug("Pipeline result backend configured: Redis")
        except ImportError:
            logger.warning(
                "taskiq-redis not installed; pipeline result backend disabled. "
                "Install with: uv add taskiq-redis"
            )

    logger.debug(f"Created pipeline broker for queue '{queue_name}' (routing_key='{routing_key}')")
    return broker


def get_broker() -> AsyncBroker:
    """Get or create the default pipeline broker singleton.

    Returns:
        The default AioPikaBroker instance.
    """
    global _broker
    if _broker is None:
        _broker = create_broker(DEFAULT_QUEUE)
    return _broker


def get_test_broker() -> AsyncBroker:
    """Create an InMemoryBroker for testing.

    Returns:
        InMemoryBroker with tasks executed in-place.
    """
    from taskiq import InMemoryBroker

    return InMemoryBroker()
