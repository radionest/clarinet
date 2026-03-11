"""
Worker queue auto-detection and startup utilities.

Determines which queues a worker should listen to based on
the machine's capabilities (GPU, DICOM) from settings.
"""

from __future__ import annotations

from clarinet.settings import settings
from clarinet.utils.logger import logger, reconfigure_for_worker

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

    Before loading, adds the tasks directory to ``sys.path`` and pre-loads
    ``record_types.py`` (if present) so that sibling imports like
    ``from record_types import master_model`` work in flow files.
    """
    import importlib.util
    import sys
    from pathlib import Path

    from clarinet.config.python_loader import _load_module, _set_file_names_from_module
    from clarinet.services.recordflow.flow_loader import find_flow_files

    for path_str in settings.recordflow_paths:
        path = Path(path_str)
        tasks_dir = path if path.is_dir() else path.parent
        flow_files = find_flow_files(path) if path.is_dir() else [path]

        # Add tasks directory to sys.path so sibling imports work
        tasks_dir_str = str(tasks_dir.resolve())
        added_to_path = tasks_dir_str not in sys.path
        if added_to_path:
            sys.path.insert(0, tasks_dir_str)

        # Pre-load record_types.py so flow files can import FileDef objects
        record_types_file = tasks_dir / "record_types.py"
        rt_module_name: str | None = None
        if record_types_file.is_file() and record_types_file.stem not in sys.modules:
            rt_module = _load_module(record_types_file, keep_in_sys=True)
            if rt_module:
                rt_module_name = record_types_file.stem
                _set_file_names_from_module(rt_module)

        try:
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
        finally:
            if rt_module_name:
                sys.modules.pop(rt_module_name, None)
            if added_to_path and tasks_dir_str in sys.path:
                sys.path.remove(tasks_dir_str)

    if settings.have_dicom:
        try:
            from clarinet.services.dicom.tasks import get_anonymize_study_task

            get_anonymize_study_task()
            logger.info("Loaded built-in DICOM pipeline tasks")
        except ImportError as e:
            logger.warning(f"Could not load DICOM tasks: {e}")


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

    # Reconfigure logging so the worker writes to clarinet_worker.log
    reconfigure_for_worker()

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
            # Create per-queue broker and re-register all tasks using public API.
            # register_task() creates a new AsyncTaskiqDecoratedTask bound to qbroker,
            # preserving the original task name and labels.
            qbroker = create_broker(queue_name)
            for task in singleton.get_all_tasks().values():
                qbroker.register_task(
                    task.original_func,
                    task_name=task.task_name,
                    **task.labels,
                )
            brokers.append(qbroker)

    # Start all brokers and begin consuming via TaskIQ receiver
    from taskiq.api.receiver import run_receiver_task

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
                    ack_time=settings.pipeline_ack_type,
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
