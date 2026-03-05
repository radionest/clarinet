"""
``pipeline_task()`` decorator factory.

Wraps an async task function with automatic ``PipelineMessage`` parsing,
``ClarinetClient`` lifecycle management, and ``TaskContext`` construction.

Example:
    @pipeline_task(queue="clarinet.gpu")
    async def run_segmentation(msg: PipelineMessage, ctx: TaskContext):
        seg_path = ctx.files.resolve("segmentation")
        ...
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from src.client import ClarinetClient
from src.settings import settings
from src.utils.logger import logger

from .broker import get_broker
from .chain import register_task
from .context import build_task_context
from .message import PipelineMessage


def pipeline_task(
    queue: str | None = None,
    **task_kwargs: Any,
) -> Callable[..., Any]:
    """Decorator factory for pipeline tasks with automatic context.

    Handles ``PipelineMessage`` parsing, ``ClarinetClient`` lifecycle,
    and ``TaskContext`` construction.  The decorated function receives
    ``(msg: PipelineMessage, ctx: TaskContext)`` instead of a raw dict.

    The wrapper returns a serialised ``PipelineMessage`` dict so that
    chain middleware can propagate it to the next step.

    Args:
        queue: Optional queue override for the broker task registration.
        **task_kwargs: Additional keyword arguments forwarded to
            ``broker.task()``.

    Returns:
        Decorator that registers the task on the singleton broker.
    """

    def decorator(fn: Callable[..., Any]) -> Any:
        @functools.wraps(fn)
        async def wrapper(msg: dict[str, Any]) -> dict[str, Any]:
            message = PipelineMessage(**msg)
            base_url = f"http://{settings.host}:{settings.port}"
            client = ClarinetClient(
                base_url=base_url,
                username=settings.admin_email,
                password=settings.admin_password,
                auto_login=False,
            )
            try:
                await client.login()
                ctx = await build_task_context(message, client)
                result = await fn(message, ctx)
                if isinstance(result, PipelineMessage):
                    return result.model_dump()
                return message.model_dump()
            except Exception:
                logger.error(f"pipeline_task '{fn.__name__}' failed", exc_info=True)
                raise
            finally:
                await client.close()

        # Register on the singleton broker
        broker = get_broker()
        kw: dict[str, Any] = {**task_kwargs}
        if queue is not None:
            kw["queue"] = queue
        decorated = broker.task(**kw)(wrapper)
        register_task(decorated)
        return decorated

    return decorator
