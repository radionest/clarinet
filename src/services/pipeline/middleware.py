"""
TaskIQ middlewares for pipeline chain advancement, logging, and dead letter queue.

PipelineChainMiddleware reads chain definition from task labels after each step
and dispatches the next step in the pipeline.

PipelineLoggingMiddleware logs task lifecycle events.

DeadLetterMiddleware routes permanently failed tasks to the dead letter queue.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from taskiq import TaskiqMiddleware

from src.utils.logger import logger

if TYPE_CHECKING:
    from taskiq import TaskiqMessage, TaskiqResult


class PipelineLoggingMiddleware(TaskiqMiddleware):
    """Logs task send/receive/complete events."""

    async def pre_send(self, message: TaskiqMessage) -> TaskiqMessage:
        """Log before a task message is sent to the broker.

        Args:
            message: The outgoing task message.

        Returns:
            The message unchanged.
        """
        pipeline_id = message.labels.get("pipeline_id", "")
        step_index = message.labels.get("step_index", "")
        prefix = f"[pipeline={pipeline_id} step={step_index}] " if pipeline_id else ""
        logger.debug(f"{prefix}Sending task '{message.task_name}' (id={message.task_id})")
        return message

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """Log after task execution completes.

        Args:
            message: The executed task message.
            result: The task execution result.
        """
        pipeline_id = message.labels.get("pipeline_id", "")
        step_index = message.labels.get("step_index", "")
        prefix = f"[pipeline={pipeline_id} step={step_index}] " if pipeline_id else ""

        if result.is_err:
            logger.error(
                f"{prefix}Task '{message.task_name}' (id={message.task_id}) failed: {result.error}"
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
    """

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

        from .broker import DLQ_QUEUE, _build_amqp_url

        try:
            dlq_payload = {
                "task_name": message.task_name,
                "task_id": message.task_id,
                "args": message.args,
                "kwargs": message.kwargs,
                "labels": message.labels,
                "error": str(result.error),
                "error_type": type(result.error).__name__ if result.error else None,
            }
            connection = await aio_pika.connect_robust(_build_amqp_url())
            async with connection:
                channel = await connection.channel()
                await channel.declare_queue(DLQ_QUEUE, durable=True)
                await channel.default_exchange.publish(
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

    Reads the chain definition from task labels (``pipeline_chain``)
    and dispatches the next step if the current step succeeded.

    Chain label format (JSON):
        {
            "pipeline_id": "ct_segmentation",
            "steps": [
                {"task_name": "fetch_dicom", "queue": "clarinet.dicom"},
                {"task_name": "run_segmentation", "queue": "clarinet.gpu"},
                {"task_name": "generate_report", "queue": "clarinet.default"}
            ]
        }
    """

    async def post_execute(self, message: TaskiqMessage, result: TaskiqResult[Any]) -> None:
        """After a step completes, dispatch the next step in the chain.

        Args:
            message: The completed task message.
            result: The task execution result.
        """
        chain_data = message.labels.get("pipeline_chain")
        if not chain_data:
            return

        if result.is_err:
            pipeline_id = message.labels.get("pipeline_id", "unknown")
            logger.warning(
                f"Pipeline '{pipeline_id}' chain stopped at step "
                f"{message.labels.get('step_index', '?')} due to error"
            )
            return

        try:
            chain = json.loads(chain_data) if isinstance(chain_data, str) else chain_data
        except (json.JSONDecodeError, TypeError):
            logger.error(f"Invalid pipeline_chain label: {chain_data}")
            return

        steps = chain.get("steps", [])
        current_index = int(message.labels.get("step_index", 0))
        next_index = current_index + 1

        if next_index >= len(steps):
            logger.info(
                f"Pipeline '{chain.get('pipeline_id', 'unknown')}' completed all {len(steps)} steps"
            )
            return

        next_step = steps[next_index]
        await self._dispatch_next_step(chain, next_step, next_index, result.return_value)

    async def _dispatch_next_step(
        self,
        chain: dict[str, Any],
        next_step: dict[str, str],
        next_index: int,
        previous_result: Any,
    ) -> None:
        """Send the next step's task to the appropriate queue.

        Args:
            chain: Full chain definition dict.
            next_step: The next step definition (task_name, queue).
            next_index: Index of the next step.
            previous_result: Return value from the previous step.
        """
        from .chain import _TASK_REGISTRY

        task_name = next_step["task_name"]
        task_func = _TASK_REGISTRY.get(task_name)

        if task_func is None:
            logger.error(
                f"Pipeline chain: task '{task_name}' not found in registry. "
                f"Ensure the task module is imported by the worker."
            )
            return

        # Build labels for the next step
        next_labels = {
            "pipeline_id": chain.get("pipeline_id", ""),
            "step_index": str(next_index),
            "pipeline_chain": json.dumps(chain),
        }

        # Route to the correct queue via routing_key
        queue = next_step.get("queue", "clarinet.default")
        routing_key = queue.rsplit(".", maxsplit=1)[-1]
        next_labels["routing_key"] = routing_key

        # Prepare the message argument — pass the previous result through
        from .message import PipelineMessage

        if isinstance(previous_result, PipelineMessage):
            msg = previous_result.model_copy(
                update={"pipeline_id": chain.get("pipeline_id"), "step_index": next_index}
            )
        elif isinstance(previous_result, dict):
            msg = PipelineMessage(**previous_result).model_copy(
                update={"pipeline_id": chain.get("pipeline_id"), "step_index": next_index}
            )
        else:
            logger.error(
                f"Pipeline chain: unexpected result type {type(previous_result)} "
                f"from step {next_index - 1}; cannot dispatch next step"
            )
            return

        try:
            await task_func.kicker().with_labels(**next_labels).kiq(msg.model_dump())
            logger.debug(
                f"Pipeline '{chain.get('pipeline_id')}' dispatched step {next_index} "
                f"('{task_name}') to queue '{queue}'"
            )
        except Exception as e:
            logger.error(f"Failed to dispatch pipeline step {next_index} ('{task_name}'): {e}")
