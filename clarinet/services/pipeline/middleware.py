"""
TaskIQ middlewares for pipeline chain advancement, logging, and dead letter queue.

DLQPublisher encapsulates a single AMQP connection to the dead letter queue.
Both DeadLetterMiddleware and PipelineChainMiddleware share one DLQPublisher
instance (composition), eliminating duplicate connection management.

PipelineChainMiddleware fetches pipeline definitions from the HTTP API
after each step and dispatches the next step in the pipeline.

PipelineLoggingMiddleware logs task lifecycle events.

DeadLetterMiddleware routes permanently failed tasks to the dead letter queue.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from taskiq import TaskiqMiddleware

from clarinet.client import ClarinetAPIError, ClarinetClient
from clarinet.settings import settings
from clarinet.utils.logger import logger

from .broker import extract_routing_key

if TYPE_CHECKING:
    from aio_pika.abc import AbstractChannel, AbstractRobustConnection
    from taskiq import TaskiqMessage, TaskiqResult


class DLQPublisher:
    """Shared AMQP publisher for the dead letter queue.

    Encapsulates a single ``connect_robust`` connection to the
    ``clarinet.dead_letter`` queue.  Pass one instance to both
    ``DeadLetterMiddleware`` and ``PipelineChainMiddleware`` so they
    share the same TCP connection and reconnection cycle.

    Lifecycle is owned by ``DeadLetterMiddleware``: it calls
    ``startup()`` / ``shutdown()`` during the TaskIQ middleware
    lifecycle.  ``DeadLetterMiddleware`` must be registered **before**
    ``PipelineChainMiddleware`` in the middleware list so the publisher
    is ready when the chain middleware needs it.

    ``connect_robust`` handles automatic reconnection and channel
    restoration — both middlewares benefit from the same event.

    Args:
        amqp_url: Optional AMQP URL override. Falls back to
            ``_build_amqp_url()`` when not provided (production default).
        queue_name: Optional queue name override. Falls back to
            ``DLQ_QUEUE`` when not provided (production default).
            Frozen after ``startup()`` — all subsequent ``publish()``
            calls use the resolved name.
    """

    def __init__(self, amqp_url: str | None = None, queue_name: str | None = None) -> None:
        self._amqp_url = amqp_url
        self._queue_name = queue_name
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None

    async def startup(self) -> None:
        """Open a persistent AMQP connection and declare the DLQ.

        Idempotent: returns immediately if the channel is already open.
        """
        if self._channel is not None:
            return

        import aio_pika

        from .broker import DLQ_QUEUE, _build_amqp_url

        url = self._amqp_url or _build_amqp_url()
        queue = self._queue_name or DLQ_QUEUE
        self._queue_name = queue  # freeze resolved name
        self._connection = await aio_pika.connect_robust(url)
        self._channel = await self._connection.channel()
        await self._channel.declare_queue(queue, durable=True)

    async def shutdown(self) -> None:
        """Close the persistent AMQP connection."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._channel = None

    async def publish(self, payload: dict[str, Any]) -> None:
        """Serialize *payload* to JSON and publish to the DLQ.

        Logs an error and returns if the channel has not been
        initialized (i.e. ``startup()`` was never called).  Does **not**
        catch exceptions — callers wrap with context-specific messages.

        Args:
            payload: Dict to publish as a JSON message.
        """
        import aio_pika

        if self._channel is None or self._queue_name is None:
            logger.error("DLQ channel not initialized, cannot publish — was startup() called?")
            return

        await self._channel.default_exchange.publish(
            aio_pika.Message(
                body=json.dumps(payload, default=str).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=self._queue_name,
        )


class PipelineLoggingMiddleware(TaskiqMiddleware):
    """Logs task send/receive/complete events."""

    @staticmethod
    def _log_prefix(message: TaskiqMessage) -> str:
        pipeline_id = message.labels.get("pipeline_id", "")
        step_index = message.labels.get("step_index", "")
        return f"[pipeline={pipeline_id} step={step_index}] " if pipeline_id else ""

    async def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
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

    This middleware owns the ``DLQPublisher`` lifecycle: it calls
    ``startup()`` and ``shutdown()`` on the shared publisher instance.

    Args:
        dlq: Shared DLQPublisher instance.
    """

    def __init__(self, dlq: DLQPublisher) -> None:
        super().__init__()
        self._dlq = dlq

    async def startup(self) -> None:
        await self._dlq.startup()

    async def shutdown(self) -> None:
        await self._dlq.shutdown()

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
            await self._dlq.publish(dlq_payload)
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

    Uses the shared ``DLQPublisher`` instance (whose lifecycle is managed
    by ``DeadLetterMiddleware``).

    Args:
        dlq: Shared DLQPublisher instance.
        client: Optional pre-configured ClarinetClient. If not provided,
            one is created from settings during ``startup()`` and closed on ``shutdown()``.
    """

    def __init__(self, dlq: DLQPublisher, client: ClarinetClient | None = None) -> None:
        super().__init__()
        self._dlq = dlq
        self._client = client
        self._owns_client = client is None

    async def _ensure_client(self) -> ClarinetClient | None:
        """Lazily create and authenticate the ClarinetClient.

        Returns:
            The authenticated client, or None if login fails.
        """
        if self._client is not None:
            return self._client

        client = ClarinetClient(
            base_url=settings.effective_api_base_url,
            username=settings.admin_email,
            password=settings.admin_password,
            auto_login=False,
            verify_ssl=settings.api_verify_ssl,
        )
        try:
            await client.login()
        except Exception as e:
            logger.error(f"Pipeline chain: failed to login ClarinetClient: {e}")
            await client.close()
            return None

        self._client = client
        return self._client

    async def shutdown(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.close()
            self._client = None

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
            await self._dlq.publish(dlq_payload)
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
        client = await self._ensure_client()
        if client is None:
            reason = f"Pipeline chain: client not initialized for '{pipeline_id}'"
            logger.error(reason)
            await self._publish_chain_failure_to_dlq(message, reason)
            return None
        try:
            return await client.get_pipeline_definition(pipeline_id)
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
            msg = PipelineMessage.model_validate(previous_result).model_copy(
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
