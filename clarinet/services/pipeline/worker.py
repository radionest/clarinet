"""
Worker queue auto-detection and startup utilities.

Determines which queues a worker should listen to based on
the machine's capabilities (GPU, DICOM) from settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from clarinet.exceptions.domain import ConfigLoadError
from clarinet.settings import settings
from clarinet.utils.logger import logger, reconfigure_for_worker

if TYPE_CHECKING:
    from taskiq import AsyncBroker


def get_worker_queues() -> list[str]:
    """Auto-detect worker queues based on machine capabilities.

    Every worker listens to the default queue. Additional queues
    are added based on ``settings.have_gpu``, ``settings.have_dicom``,
    and ``settings.have_quarto``.

    Returns:
        List of queue names this worker should consume from.
    """
    queues = [settings.default_queue_name]

    if settings.have_gpu:
        queues.append(settings.gpu_queue_name)
        logger.info("GPU capability detected — worker will listen to GPU queue")

    if settings.have_dicom:
        queues.append(settings.dicom_queue_name)
        logger.info("DICOM capability detected — worker will listen to DICOM queue")

    if settings.have_quarto:
        queues.append(settings.quarto_queue_name)
        logger.info("Quarto capability detected — worker will listen to Quarto queue")

    return queues


async def warn_if_stale(queues: list[str]) -> None:
    """Log a loud ERROR if our fingerprint differs from the running API's.

    Diagnostic only — broker-level isolation (version-gated queue names) already
    prevents a stale worker from receiving tasks. Never raises: a 404 (old API
    without the endpoint) or an unreachable API downgrades to a WARNING.
    """
    if not settings.pipeline_version_check_enabled:
        return

    from clarinet.client import ClarinetAPIError, ClarinetClient
    from clarinet.services.pipeline.fingerprint import compute_fingerprint

    client = None
    try:
        client = ClarinetClient(
            base_url=settings.effective_api_base_url,
            service_token=settings.effective_service_token,
        )
        api_fp = await client.get_worker_fingerprint()
        mine = compute_fingerprint()
        if api_fp != mine:
            logger.error(
                f"Worker fingerprint {mine} != API {api_fp}; listening on stale "
                f"queues {queues} — will NOT receive new tasks until the worker's "
                f"code is updated and the process restarted."
            )
        else:
            logger.info(f"Worker fingerprint matches API: {mine}")
    except ClarinetAPIError as e:
        # _request wraps transport/HTTP errors (incl. 404 from an old API) here.
        logger.warning(f"Could not verify worker fingerprint against API: {e}")
    except Exception as e:  # diagnostic must never crash worker startup
        logger.warning(f"Unexpected error verifying worker fingerprint: {e}")
    finally:
        if client is not None:
            await client.close()


def load_task_modules() -> None:
    """Import flow files to register pipeline tasks on per-queue brokers.

    Discovers ``*_flow.py`` files from ``settings.recordflow_paths`` and imports
    each as a ``clarinet_plan.`` submodule so that ``@pipeline_task()`` and
    ``@broker.task()`` decorators populate the per-queue broker registry.

    ``record_types`` is imported once via ``_ensure_record_types_imported`` (off
    the same anchor root), so flow files reference record types through
    ``from clarinet_plan.record_types import master_model``. Every
    ``recordflow_path`` must live inside ``config_tasks_path`` — a path outside
    the anchor root is reported as a ``ConfigLoadError``.

    Raises:
        ConfigLoadError: Aggregated error when any flow file fails to import (or
            a path lives outside the plan root) — every path and file is
            attempted first, so one crash reports all broken files.
    """
    from pathlib import Path

    from clarinet.config.plan_package import (
        ensure_plan_root,
        import_plan_module,
        module_name_for,
    )
    from clarinet.config.python_loader import _ensure_record_types_imported
    from clarinet.services.recordflow.flow_loader import find_flow_files

    # Anchor + record types (sets FileDef names) before importing any flow file.
    _ensure_record_types_imported()

    failures: list[ConfigLoadError] = []
    for path_str in settings.recordflow_paths:
        path = Path(path_str)
        tasks_dir = path if path.is_dir() else path.parent
        flow_files = find_flow_files(path) if path.is_dir() else [path]

        try:
            # Validates recordflow_path is inside config_tasks_path.
            ensure_plan_root(tasks_dir)
        except ConfigLoadError as e:
            failures.append(e)
            continue

        for flow_file in flow_files:
            try:
                import_plan_module(module_name_for(flow_file), path_hint=flow_file)
            except ConfigLoadError as e:
                failures.append(e)
                continue
            logger.info(f"Loaded pipeline tasks from {flow_file}")

    if failures:
        raise ConfigLoadError.aggregate(failures, kind="pipeline task module")

    if settings.have_dicom:
        try:
            from clarinet.services.dicom.pipeline import (
                anonymize_study_pipeline as _asp,  # noqa: F401
            )

            logger.info("Loaded built-in DICOM pipeline tasks")
        except ImportError as e:
            logger.warning(f"Could not load DICOM tasks: {e}")

        # Built-in DICOM pipeline tasks (convert_series uses C-GET,
        # cache_dicomweb prefetches studies into the DICOMweb disk cache)
        try:
            from clarinet.services.pipeline.tasks import cache_dicomweb as _cw  # noqa: F401
            from clarinet.services.pipeline.tasks import convert_series as _cs  # noqa: F401

            logger.info("Loaded built-in pipeline tasks (convert_series, cache_dicomweb)")
        except ImportError as e:
            logger.warning(f"Could not load built-in pipeline tasks: {e}")

    try:
        from clarinet.services.pipeline.tasks import call_registered_callable as _crc  # noqa: F401

        logger.info("Loaded built-in pipeline task call_registered_callable")
    except ImportError as e:
        logger.warning(f"Could not load call_registered_callable task: {e}")

    try:
        from clarinet.services.pipeline.tasks import quarto_render as _qr  # noqa: F401

        logger.info("Loaded built-in pipeline task render_quarto_report")
    except ImportError as e:
        logger.warning(f"Could not load quarto_render task: {e}")


async def run_worker(
    queues: list[str] | None = None,
    workers: int = 2,
    start_scp: bool = False,
    log_file: str | None = None,
) -> None:
    """Start a TaskIQ worker process for the given queues.

    Loads task modules first — each ``@pipeline_task`` decorator registers
    its task on the per-queue broker for its declared queue.  Then we
    look up each requested queue's broker via ``get_broker_for`` (returns
    the same broker the decorators populated) and consume from it.

    Args:
        queues: Queue names to listen on (auto-detected if None).
        workers: Number of concurrent worker tasks per queue.
        start_scp: Start a Storage SCP for C-MOVE retrieval.
        log_file: Optional override for the worker log path
            (forwarded to :func:`reconfigure_for_worker`).
    """
    import asyncio
    import signal
    import sys

    from .broker import get_broker_for

    reconfigure_for_worker(log_file=log_file)

    # Start Storage SCP before loading tasks (they may use C-MOVE immediately)
    scp = None
    if start_scp:
        from clarinet.services.dicom.scp import get_storage_scp

        scp = get_storage_scp()
        try:
            scp.start(settings.dicom_aet, settings.dicom_port, settings.dicom_ip)
        except OSError as e:
            logger.error(
                f"Failed to start Storage SCP on port {settings.dicom_port}: {e}. "
                "Ensure the port is not already in use by another process."
            )
            raise

    receiver_tasks: list[asyncio.Task[None]] = []
    brokers: list[AsyncBroker] = []
    try:
        try:
            from clarinet.config.plan_package import activate_plan_package

            # Anchor the clarinet_plan package at the config root before
            # importing any flow file (mirrors the API lifespan).
            activate_plan_package(settings.config_tasks_path)
            load_task_modules()
        except ConfigLoadError as e:
            logger.error(f"Cannot start worker — project task modules failed to load: {e}")
            raise SystemExit(1) from e

        if queues is None:
            queues = get_worker_queues()

        logger.info(f"Starting pipeline worker on queues: {queues} (workers={workers})")

        await warn_if_stale(queues)

        brokers = [get_broker_for(q) for q in queues]

        # Start all brokers and begin consuming via TaskIQ receiver
        from taskiq.api.receiver import run_receiver_task

        for broker in brokers:
            broker.is_worker_process = True
            await broker.startup()
            receiver_tasks.append(
                asyncio.create_task(
                    run_receiver_task(
                        broker,
                        max_async_tasks=workers,
                        run_startup=False,
                        ack_time=settings.pipeline_ack_type,
                    )
                )
            )

        logger.info(f"Pipeline worker started, listening on {len(brokers)} queue(s)")
        for queue_name, broker in zip(queues, brokers, strict=True):
            logger.info(f"Queue '{queue_name}' tasks: {list(broker.get_all_tasks().keys())}")

        shutdown_event = asyncio.Event()

        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(shutdown_event.set))
        else:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, shutdown_event.set)

        await shutdown_event.wait()
    finally:
        for receiver_task in receiver_tasks:
            receiver_task.cancel()
        for broker in brokers:
            await broker.shutdown()
        if scp is not None:
            from clarinet.services.dicom.scp import shutdown_storage_scp

            shutdown_storage_scp()
        logger.info("Pipeline worker stopped")
