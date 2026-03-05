"""Async filesystem I/O helper with dedicated thread pool.

Provides a dedicated ``ThreadPoolExecutor`` for blocking filesystem
syscalls (``Path.is_file``, ``Path.glob``, file reads) so they don't
block the async event loop.
"""

import asyncio
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from src.utils.logger import logger

_FS_MAX_WORKERS = 8
_SLOW_THRESHOLD_SEC = 1.0


def _make_executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=_FS_MAX_WORKERS, thread_name_prefix="fs-io")


_fs_executor: ThreadPoolExecutor = _make_executor()


async def run_in_fs_thread[T](fn: Callable[..., T], *args: Any) -> T:
    """Run a sync function in the dedicated FS thread pool.

    Args:
        fn: Synchronous callable to execute.
        *args: Positional arguments forwarded to *fn*.

    Returns:
        The return value of *fn(*args)*.
    """
    loop = asyncio.get_running_loop()
    t0 = time.monotonic()
    result = await loop.run_in_executor(_fs_executor, fn, *args)
    elapsed = time.monotonic() - t0
    if elapsed > _SLOW_THRESHOLD_SEC:
        logger.warning(
            f"Slow FS operation: {fn.__qualname__} took {elapsed:.2f}s "
            f"(pool: {len(_fs_executor._threads)}/{_FS_MAX_WORKERS} threads, "
            f"queue={_fs_executor._work_queue.qsize()})"
        )
    return result


def shutdown_fs_executor() -> None:
    """Shutdown the FS thread pool and replace with a fresh one.

    Re-creates the executor so subsequent app lifespans (e.g. in tests)
    can continue to schedule work.
    """
    global _fs_executor
    _fs_executor.shutdown(wait=False)
    _fs_executor = _make_executor()
    logger.info("FS thread pool shut down")
