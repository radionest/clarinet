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

    from clarinet.config.python_loader import preload_record_types
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

        try:
            with preload_record_types(tasks_dir):
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
            if added_to_path and tasks_dir_str in sys.path:
                sys.path.remove(tasks_dir_str)

    if settings.have_dicom:
        try:
            from clarinet.services.dicom.tasks import get_anonymize_study_task

            get_anonymize_study_task()
            logger.info("Loaded built-in DICOM pipeline tasks")
        except ImportError as e:
            logger.warning(f"Could not load DICOM tasks: {e}")

        # Built-in DICOM pipeline tasks (convert_series uses C-GET)
        try:
            from clarinet.services.pipeline.tasks import convert_series as _cs  # noqa: F401

            logger.info("Loaded built-in pipeline tasks (convert_series)")
        except ImportError as e:
            logger.warning(f"Could not load built-in pipeline tasks: {e}")


async def run_worker(
    queues: list[str] | None = None,
    workers: int = 2,
    start_scp: bool = False,
) -> None:
    """Start a TaskIQ worker process for the given queues.

    Loads task modules to register handlers on the singleton broker,
    then starts broker instances for each queue.

    Args:
        queues: Queue names to listen on (auto-detected if None).
        workers: Number of concurrent worker tasks per queue.
        start_scp: Start a Storage SCP for C-MOVE retrieval.
    """
    import asyncio
    import signal
    import sys

    from .broker import create_broker, get_broker

    # Reconfigure logging so the worker writes to clarinet_worker.log
    reconfigure_for_worker()

    # Start Storage SCP before loading tasks (they may use C-MOVE immediately)
    scp = None
    if start_scp:
        from clarinet.services.dicom.scp import get_storage_scp

        scp = get_storage_scp()
        try:
            scp.start(aet=settings.dicom_aet, port=settings.dicom_port, ip=settings.dicom_ip)
        except OSError as e:
            logger.error(
                f"Failed to start Storage SCP on port {settings.dicom_port}: {e}. "
                "Ensure the port is not already in use by another process."
            )
            raise

    receiver_tasks: list[asyncio.Task[None]] = []
    brokers: list = []
    try:
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
