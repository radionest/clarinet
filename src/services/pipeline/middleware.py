"""
TaskIQ middlewares for pipeline chain advancement, logging, and dead letter queue.

PipelineChainMiddleware fetches pipeline definitions from the HTTP API
after each step and dispatches the next step in the pipeline.

PipelineLoggingMiddleware logs task lifecycle events.

DeadLetterMiddleware routes permanently failed tasks to the dead letter queue.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from taskiq import TaskiqMiddleware

from src.client import ClarinetAPIError, ClarinetClient
from src.settings import settings
from src.utils.logger import logger

from .broker import extract_routing_key

if TYPE_CHECKING:
    from aio_pika.abc import AbstractChannel, AbstractRobustConnection
    from taskiq import TaskiqMessage, TaskiqResult


class PipelineLoggingMiddleware(TaskiqMiddleware):
    """Logs task send/receive/complete events."""

    @staticmethod
    def _log_prefix(message: TaskiqMessage) -> str:
        pipeline_id = message.labels.get("pipeline_id", "")
        step_index = message.labels.get("step_index", "")
        return f"[pipeline={pipeline_id} step={step_index}] " if pipeline_id else ""

    async def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        """Log before a task message is sent to the broker.

        Args:
            message: The outgoing task message.

        Returns:
            The message unchanged.
        """
        prefix = self._log_prefix(message)
        logger.debug(f"{prefix}Sending task '{message.task_name}' (id={message.task_id})")
        return message

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Log after task execution completes.

        Args:
            message: The executed task message.
            result: The task execution result.
        """
        prefix = self._log_prefix(message)

        if result.is_err:
            error = result.error
            detail = getattr(error, "detail", None)
            detail_suffix = f" (detail: {detail})" if detail is not None else ""
            logger.error(
                f"{prefix}Task '{message.task_name}' (id={message.task_id}) "
                f"failed: {error}{detail_suffix}"
            )
        else:
            logger.info(
                f"{prefix}Task '{message.task_name}' (id={message.task_id}) "
                f"completed in {result.execution_time:.3f}s"
            )


class DeadLetterMiddleware(TaskiqMiddleware):
    """Routes permanently failed tasks to the dead letter queue.

    SmartRetryMiddleware sets result.error = NoResultError() when scheduling
    a retry. If post_execute sees a real error (not NoResultError), it means
    retries are exhausted or disabled — route to DLQ.

    Args:
        amqp_url: Optional AMQP URL override. Falls back to ``_build_amqp_url()``
            when not provided (production default).
    """

    def __init__(self, amqp_url: str | None = None) -> None:
        super().__init__()
        self._amqp_url = amqp_url
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None

    async def startup(self) -> None:
        """Open a persistent AMQP connection and declare the DLQ."""
        import aio_pika

        from .broker import DLQ_QUEUE, _build_amqp_url

        url = self._amqp_url or _build_amqp_url()
        self._connection = await aio_pika.connect_robust(url)
        self._channel = await self._connection.channel()
        await self._channel.declare_queue(DLQ_QUEUE, durable=True)

    async def shutdown(self) -> None:
        """Close the persistent AMQP connection."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Check if a failed task should be routed to the DLQ.

        Args:
            message: The executed task message.
            result: The task execution result.
        """
        if not result.is_err:
            return

        # SmartRetryMiddleware replaces error with NoResultError on retry
        from taskiq.exceptions import NoResultError

        if isinstance(result.error, NoResultError):
            return  # retry scheduled, skip DLQ

        await self._publish_to_dlq(message, result)

    async def _publish_to_dlq(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Publish failed message to clarinet.dead_letter queue.

        Args:
            message: The failed task message.
            result: The task execution result with error.
        """
        import aio_pika

        from .broker import DLQ_QUEUE

        if self._channel is None:
            logger.error(
                f"DLQ channel not initialized, cannot route task '{message.task_name}' "
                f"(id={message.task_id}) — was startup() called?"
            )
            return

        try:
            error = result.error
            dlq_payload: dict[str, Any] = {
                "task_name": message.task_name,
                "task_id": message.task_id,
                "args": message.args,
                "kwargs": message.kwargs,
                "labels": message.labels,
                "error": str(error),
                "error_type": type(error).__name__ if error else None,
                "error_detail": getattr(error, "detail", None),
                "error_status_code": getattr(error, "status_code", None),
            }
            await self._channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(dlq_payload, default=str).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=DLQ_QUEUE,
            )
            logger.warning(
                f"Task '{message.task_name}' (id={message.task_id}) "
                f"sent to dead letter queue: {result.error}"
            )
        except Exception as e:
            logger.error(f"Failed to route task '{message.task_name}' to DLQ: {e}")


class PipelineChainMiddleware(TaskiqMiddleware):
    """Advances pipeline chains after each step completes.

    Fetches the pipeline definition from the HTTP API using the ``pipeline_id``
    label, then dispatches the next step if the current step succeeded.

    If chain advancement fails for any reason, publishes a ``chain_failure``
    record to the dead letter queue so the failure is observable.

    Args:
        client: Optional pre-configured ClarinetClient. If not provided,
            one is created from settings during ``startup()`` and closed on ``shutdown()``.
        amqp_url: Optional AMQP URL override for DLQ publishing. Falls back to
            ``_build_amqp_url()`` when not provided (production default).
    """

    def __init__(self, client: ClarinetClient | None = None, amqp_url: str | None = None) -> None:
        super().__init__()
        self._client = client
        self._owns_client = client is None
        self._amqp_url = amqp_url
        self._dlq_connection: AbstractRobustConnection | None = None
        self._dlq_channel: AbstractChannel | None = None

    async def startup(self) -> None:
        """Create the ClarinetClient and open a persistent DLQ connection."""
        import aio_pika

        from .broker import DLQ_QUEUE, _build_amqp_url

        if self._client is None:
            self._client = ClarinetClient(
                base_url=f"http://{settings.host}:{settings.port}/api",
                username=settings.admin_email,
                password=settings.admin_password,
                auto_login=False,
            )
            await self._client.login()

        url = self._amqp_url or _build_amqp_url()
        self._dlq_connection = await aio_pika.connect_robust(url)
        self._dlq_channel = await self._dlq_connection.channel()
        await self._dlq_channel.declare_queue(DLQ_QUEUE, durable=True)

    async def shutdown(self) -> None:
        """Close the ClarinetClient and DLQ connection."""
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None
        if self._dlq_connection is not None:
            await self._dlq_connection.close()
            self._dlq_connection = None
            self._dlq_channel = None

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """After a step completes, dispatch the next step in the chain.

        Args:
            message: The completed task message.
            result: The task execution result.
        """
        pipeline_id = message.labels.get("pipeline_id")
        if not pipeline_id:
            return

        if result.is_err:
            from taskiq.exceptions import NoResultError

            if isinstance(result.error, NoResultError):
                return  # retry scheduled, don't stop chain

            logger.warning(
                f"Pipeline '{pipeline_id}' chain stopped at step "
                f"{message.labels.get('step_index', '?')} due to error"
            )
            return

        # Fetch pipeline definition from API
        steps = await self._fetch_pipeline_steps(message, pipeline_id)
        if steps is None:
            return

        current_index = int(message.labels.get("step_index", 0))
        next_index = current_index + 1

        if next_index >= len(steps):
            logger.info(f"Pipeline '{pipeline_id}' completed all {len(steps)} steps")
            return

        next_step = steps[next_index]
        await self._dispatch_next_step(
            message, pipeline_id, next_step, next_index, result.return_value
        )

    async def _publish_chain_failure_to_dlq(self, message: TaskiqMessage, reason: str) -> None:
        """Publish a chain_failure record to the dead letter queue.

        Called whenever chain advancement fails so the failure is observable
        without access to logs.

        Args:
            message: The task message that completed (whose chain advancement failed).
            reason: Human-readable description of what went wrong.
        """
        import aio_pika

        from .broker import DLQ_QUEUE

        if self._dlq_channel is None:
            logger.error(
                "DLQ channel not initialized for PipelineChainMiddleware, "
                "cannot publish chain_failure — was startup() called?"
            )
            return

        pipeline_id = message.labels.get("pipeline_id", "unknown")
        step_index = message.labels.get("step_index", "?")
        dlq_payload: dict[str, Any] = {
            "task_name": message.task_name,
            "task_id": message.task_id,
            "labels": message.labels,
            "error_type": "chain_failure",
            "error": reason,
        }
        try:
            await self._dlq_channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(dlq_payload, default=str).encode(),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                ),
                routing_key=DLQ_QUEUE,
            )
            logger.error(
                f"Pipeline chain failure for '{pipeline_id}' step {step_index} → DLQ: {reason}"
            )
        except Exception as e:
            logger.error(f"Pipeline chain: failed to write chain_failure to DLQ: {e}")

    async def _fetch_pipeline_steps(
        self, message: TaskiqMessage, pipeline_id: str
    ) -> list[dict[str, str]] | None:
        """Fetch pipeline steps via ClarinetClient.

        Args:
            message: The current task message (used for DLQ context on failure).
            pipeline_id: Pipeline name to look up.

        Returns:
            List of step dicts, or None on failure.
        """
        if self._client is None:
            reason = f"Pipeline chain: client not initialized for '{pipeline_id}'"
            logger.error(reason)
            await self._publish_chain_failure_to_dlq(message, reason)
            return None
        try:
            return await self._client.get_pipeline_definition(pipeline_id)
        except ClarinetAPIError as e:
            if e.status_code == 404:
                reason = f"Pipeline chain: definition '{pipeline_id}' not found (404)"
                logger.error(f"{reason} → DLQ")
            else:
                reason = f"Pipeline chain: failed to fetch '{pipeline_id}' [{e.status_code}]: {e}"
                logger.error(f"{reason} → DLQ")
            await self._publish_chain_failure_to_dlq(message, reason)
            return None
        except Exception as e:
            reason = f"Pipeline chain: unexpected error fetching '{pipeline_id}': {e}"
            logger.error(f"{reason} → DLQ")
            await self._publish_chain_failure_to_dlq(message, reason)
            return None

    async def _dispatch_next_step(
        self,
        message: TaskiqMessage,
        pipeline_id: str,
        next_step: dict[str, str],
        next_index: int,
        previous_result: Any,
    ) -> None:
        """Send the next step's task to the appropriate queue.

        Args:
            message: The current task message (used for DLQ context on failure).
            pipeline_id: Pipeline identifier.
            next_step: The next step definition (task_name, queue).
            next_index: Index of the next step.
            previous_result: Return value from the previous step.
        """
        from .chain import _TASK_REGISTRY

        task_name = next_step["task_name"]
        task_func = _TASK_REGISTRY.get(task_name)

        if task_func is None:
            reason = (
                f"Pipeline chain: task '{task_name}' not in registry for "
                f"'{pipeline_id}' step {next_index}"
            )
            logger.error(f"{reason} → DLQ")
            await self._publish_chain_failure_to_dlq(message, reason)
            return

        # Build labels for the next step — no pipeline_chain, just ID + index
        next_labels: dict[str, str] = {
            "pipeline_id": pipeline_id,
            "step_index": str(next_index),
        }

        # Route to the correct queue via routing_key
        queue = next_step.get("queue", "clarinet.default")
        routing_key = extract_routing_key(queue)
        next_labels["routing_key"] = routing_key

        # Prepare the message argument — pass the previous result through
        from .message import PipelineMessage

        if isinstance(previous_result, PipelineMessage):
            msg = previous_result.model_copy(
                update={"pipeline_id": pipeline_id, "step_index": next_index}
            )
        elif isinstance(previous_result, dict):
            msg = PipelineMessage(**previous_result).model_copy(
                update={"pipeline_id": pipeline_id, "step_index": next_index}
            )
        else:
            reason = (
                f"Pipeline chain: unexpected result type {type(previous_result).__name__} "
                f"at step {next_index - 1} of '{pipeline_id}'"
            )
            logger.error(f"{reason} → DLQ")
            await self._publish_chain_failure_to_dlq(message, reason)
            return

        try:
            await task_func.kicker().with_labels(**next_labels).kiq(msg.model_dump())
            logger.debug(
                f"Pipeline '{pipeline_id}' dispatched step {next_index} "
                f"('{task_name}') to queue '{queue}'"
            )
        except Exception as e:
            reason = (
                f"Pipeline chain: dispatch failed for '{pipeline_id}' "
                f"step {next_index} ('{task_name}'): {e}"
            )
            logger.error(f"{reason} → DLQ")
            await self._publish_chain_failure_to_dlq(message, reason)
