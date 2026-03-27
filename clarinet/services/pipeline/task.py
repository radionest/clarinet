"""
``pipeline_task()`` decorator factory.

Wraps a task function with automatic ``PipelineMessage`` parsing,
``ClarinetClient`` lifecycle management, and ``TaskContext`` construction.

Sync handlers are auto-detected and run in a thread via ``asyncio.to_thread()``,
receiving a ``SyncTaskContext`` instead of ``TaskContext``.

Example:
    @pipeline_task(queue="clarinet.gpu")
    async def run_segmentation(msg: PipelineMessage, ctx: TaskContext):
        seg_path = ctx.files.resolve("segmentation")
        ...

    @pipeline_task(auto_submit=True)
    def compare_data(msg: PipelineMessage, ctx: SyncTaskContext) -> dict:
        return {"result": "ok"}  # auto-submitted via submit_record_data
"""

from __future__ import annotations

import asyncio
import functools
import inspect
from collections.abc import Callable
from typing import Any

from clarinet.client import ClarinetClient
from clarinet.settings import settings
from clarinet.utils.logger import logger

from .broker import get_broker
from .chain import register_task
from .context import FileResolver, build_task_context
from .message import PipelineMessage
from .sync_wrappers import build_sync_context


def pipeline_task(
    queue: str | None = None,
    *,
    auto_submit: bool = False,
    **task_kwargs: Any,
) -> Callable[..., Any]:
    """Decorator factory for pipeline tasks with automatic context.

    Handles ``PipelineMessage`` parsing, ``ClarinetClient`` lifecycle,
    and ``TaskContext`` construction.  Async handlers receive
    ``(msg: PipelineMessage, ctx: TaskContext)``; sync handlers receive
    ``(msg: PipelineMessage, ctx: SyncTaskContext)`` and run in a thread.

    The wrapper returns a serialised ``PipelineMessage`` dict so that
    chain middleware can propagate it to the next step.

    Args:
        queue: Optional queue override for the broker task registration.
        auto_submit: If ``True`` and handler returns a ``dict``, automatically
            calls ``submit_record_data(msg.record_id, result)``.
        **task_kwargs: Additional keyword arguments forwarded to
            ``broker.task()``.

    Returns:
        Decorator that registers the task on the singleton broker.
    """

    def decorator(fn: Callable[..., Any]) -> Any:
        is_async = inspect.iscoroutinefunction(fn)

        @functools.wraps(fn)
        async def wrapper(msg: dict[str, Any]) -> dict[str, Any]:
            message = PipelineMessage.model_validate(msg)
            client = ClarinetClient(
                base_url=settings.effective_api_base_url,
                username=settings.admin_email,
                password=settings.admin_password,
                auto_login=False,
                verify_ssl=settings.api_verify_ssl,
            )
            try:
                await client.login()
                ctx = await build_task_context(message, client)
                pre_checksums = await ctx.files.snapshot_checksums()

                if is_async:
                    result = await fn(message, ctx)
                else:
                    loop = asyncio.get_running_loop()
                    sync_ctx = build_sync_context(ctx, loop)
                    result = await asyncio.to_thread(fn, message, sync_ctx)

                # auto_submit: dict result → submit_record_data
                if auto_submit and isinstance(result, dict):
                    if message.record_id is not None:
                        await client.submit_record_data(message.record_id, result)
                    else:
                        logger.warning(
                            f"pipeline_task '{fn.__name__}': auto_submit skipped — "
                            f"msg.record_id is None"
                        )

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
