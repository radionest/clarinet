"""
Pipeline Service — Distributed task pipeline for Clarinet.

Provides a TaskIQ-based task queue with chain middleware for
multi-step distributed pipelines (GPU processing, DICOM operations, etc.).

Queue names are project-namespaced via ``settings.pipeline_task_namespace``.
Each queue gets its own broker — looked up via ``get_broker_for(queue_name)``.

Example:
    from clarinet.services.pipeline import Pipeline, PipelineMessage, get_pipeline

    # Define a pipeline (each task already declares its queue via the decorator)
    imaging_pipeline = (
        Pipeline("ct_segmentation")
        .step(fetch_dicom)
        .step(run_segmentation)
        .step(generate_report)
    )

    # Execute
    msg = PipelineMessage(patient_id="P001", study_uid="1.2.3")
    await imaging_pipeline.run(msg)

    # Look up by name
    pipeline = get_pipeline("ct_segmentation")
"""

from clarinet.exceptions.domain import PipelineConfigError, PipelineError, PipelineStepError

from .broker import (
    create_broker,
    get_all_brokers,
    get_broker,
    get_broker_for,
    get_test_broker,
    is_registered,
    reset_brokers,
)
from .chain import (
    Pipeline,
    get_all_pipelines,
    get_pipeline,
    persist_definitions,
    register_task,
    sync_pipeline_definitions,
)
from .context import FileResolver, RecordQuery, TaskContext, build_task_context

# ``FileResolver`` is re-exported here for backward compatibility with
# downstream projects that imported it as
# ``from clarinet.services.pipeline import FileResolver``. New code should
# use ``from clarinet.services.common.file_resolver import FileResolver``
# — the canonical home — to avoid pulling the broker / TaskIQ machinery
# into modules that only need path rendering.
from .message import PipelineMessage, build_pipeline_message_from_record
from .middleware import DeadLetterMiddleware, DLQPublisher
from .sync_wrappers import SyncPipelineClient, SyncRecordQuery, SyncTaskContext
from .task import pipeline_task
from .worker import get_worker_queues, load_task_modules, run_worker

__all__ = [
    "DLQPublisher",
    "DeadLetterMiddleware",
    "FileResolver",
    "Pipeline",
    "PipelineConfigError",
    "PipelineError",
    "PipelineMessage",
    "PipelineStepError",
    "RecordQuery",
    "SyncPipelineClient",
    "SyncRecordQuery",
    "SyncTaskContext",
    "TaskContext",
    "build_pipeline_message_from_record",
    "build_task_context",
    "create_broker",
    "get_all_brokers",
    "get_all_pipelines",
    "get_broker",
    "get_broker_for",
    "get_pipeline",
    "get_test_broker",
    "get_worker_queues",
    "is_registered",
    "load_task_modules",
    "persist_definitions",
    "pipeline_task",
    "register_task",
    "reset_brokers",
    "run_worker",
    "sync_pipeline_definitions",
]
