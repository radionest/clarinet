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

from clarinet.client import ClarinetClient
from clarinet.settings import settings
from clarinet.utils.logger import logger

from .broker import get_broker
from .chain import register_task
from .context import FileResolver, build_task_context
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
            message = PipelineMessage.model_validate(msg)
            client = ClarinetClient(
                base_url=settings.api_base_url,
                username=settings.admin_email,
                password=settings.admin_password,
                auto_login=False,
            )
            try:
                await client.login()
                ctx = await build_task_context(message, client)
                pre_checksums = await ctx.files.snapshot_checksums()
                result = await fn(message, ctx)
                changed = await _detect_file_changes(ctx.files, pre_checksums)
                if changed and message.patient_id:
                    try:
                        await client.notify_file_changes(message.patient_id, changed)
                    except Exception:
                        logger.warning("Failed to notify file changes", exc_info=True)
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


async def _detect_file_changes(files: FileResolver, pre: dict[str, str | None]) -> list[str]:
    """Compare pre-task checksums with current state to detect file changes.

    Args:
        files: FileResolver with access tracking from the completed task.
        pre: Pre-task checksums from ``snapshot_checksums()``.

    Returns:
        List of file definition names whose checksums changed.
    """
    from clarinet.utils.file_checksums import compute_file_checksum

    changed: list[str] = []
    for name, path in files.accessed_files.items():
        old = pre.get(name)
        new = await compute_file_checksum(path) if path.is_file() else None
        if old != new:
            changed.append(name)
    return changed
