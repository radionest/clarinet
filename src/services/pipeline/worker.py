"""
Worker queue auto-detection and startup utilities.

Determines which queues a worker should listen to based on
the machine's capabilities (GPU, DICOM) from settings.
"""

from __future__ import annotations

from src.settings import settings
from src.utils.logger import logger

from .broker import DEFAULT_QUEUE, DICOM_QUEUE, GPU_QUEUE


def get_worker_queues() -> list[str]:
    """Auto-detect worker queues based on machine capabilities.

    Every worker listens to the default queue. Additional queues
    are added based on ``settings.have_gpu`` and ``settings.have_dicom``.

    Returns:
        List of queue names this worker should consume from.
    """
    queues = [DEFAULT_QUEUE]

    if settings.have_gpu:
        queues.append(GPU_QUEUE)
        logger.info("GPU capability detected — worker will listen to GPU queue")

    if settings.have_dicom:
        queues.append(DICOM_QUEUE)
        logger.info("DICOM capability detected — worker will listen to DICOM queue")

    return queues


def _load_task_modules() -> None:
    """Import flow files to register pipeline tasks on the singleton broker.

    Discovers ``*_flow.py`` files from ``settings.recordflow_paths`` and
    loads them via ``importlib.util`` so that ``@broker.task()`` decorators
    populate the singleton broker's task registry.
    """
    import importlib.util
    import sys
    from pathlib import Path

    from src.services.recordflow.flow_loader import find_flow_files

    for path_str in settings.recordflow_paths:
        path = Path(path_str)
        flow_files = find_flow_files(path) if path.is_dir() else [path]
        for flow_file in flow_files:
            module_name = flow_file.stem
            try:
                spec = importlib.util.spec_from_file_location(module_name, flow_file)
                if spec is None or spec.loader is None:
                    logger.error(f"Cannot create module spec for {flow_file}")
                    continue

                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                logger.info(f"Loaded pipeline tasks from {flow_file}")
            except Exception as e:
                logger.error(f"Failed to load tasks from {flow_file}: {e}")


async def run_worker(
    queues: list[str] | None = None,
    workers: int = 2,
) -> None:
    """Start a TaskIQ worker process for the given queues.

    Loads task modules to register handlers on the singleton broker,
    then starts broker instances for each queue.

    Args:
        queues: Queue names to listen on (auto-detected if None).
        workers: Number of concurrent worker tasks per queue.
    """
    import asyncio
    import signal

    from .broker import create_broker, get_broker

    _load_task_modules()

    if queues is None:
        queues = get_worker_queues()

    logger.info(f"Starting pipeline worker on queues: {queues} (workers={workers})")

    # Singleton broker has all tasks registered via @broker.task() decorators
    singleton = get_broker()

    brokers = []
    for queue_name in queues:
        if queue_name == DEFAULT_QUEUE:
            brokers.append(singleton)
        else:
            # Create per-queue broker and copy task registrations
            qbroker = create_broker(queue_name)
            for task_name, task in singleton.get_all_tasks().items():
                qbroker.local_task_registry[task_name] = task
            brokers.append(qbroker)

    # Start all brokers and begin consuming via TaskIQ receiver
    from taskiq.acks import AcknowledgeType
    from taskiq.api.receiver import run_receiver_task

    _ACK_TYPE_MAP = {
        "when_received": AcknowledgeType.WHEN_RECEIVED,
        "when_executed": AcknowledgeType.WHEN_EXECUTED,
        "when_saved": AcknowledgeType.WHEN_SAVED,
    }
    ack_type = _ACK_TYPE_MAP.get(settings.pipeline_ack_type, AcknowledgeType.WHEN_EXECUTED)

    receiver_tasks: list[asyncio.Task[None]] = []
    for broker in brokers:
        broker.is_worker_process = True
        await broker.startup()
        receiver_tasks.append(
            asyncio.create_task(
                run_receiver_task(
                    broker,
                    max_async_tasks=workers,
                    run_startup=False,
                    ack_time=ack_type,
                )
            )
        )

    logger.info(f"Pipeline worker started, listening on {len(brokers)} queue(s)")
    logger.info(f"Registered tasks: {list(singleton.get_all_tasks().keys())}")

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    try:
        await shutdown_event.wait()
    finally:
        for receiver_task in receiver_tasks:
            receiver_task.cancel()
        for broker in brokers:
            await broker.shutdown()
        logger.info("Pipeline worker stopped")
