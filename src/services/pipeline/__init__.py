"""
Pipeline Service — Distributed task pipeline for Clarinet.

Provides a TaskIQ-based task queue with chain middleware for
multi-step distributed pipelines (GPU processing, DICOM operations, etc.).

Example:
    from src.services.pipeline import Pipeline, PipelineMessage, get_pipeline

    # Define a pipeline
    imaging_pipeline = (
        Pipeline("ct_segmentation")
        .step(fetch_dicom, queue="clarinet.dicom")
        .step(run_segmentation, queue="clarinet.gpu")
        .step(generate_report, queue="clarinet.default")
    )

    # Execute
    msg = PipelineMessage(patient_id="P001", study_uid="1.2.3")
    await imaging_pipeline.run(msg)

    # Look up by name
    pipeline = get_pipeline("ct_segmentation")
"""

from .broker import (
    DEFAULT_QUEUE,
    DICOM_QUEUE,
    DLQ_QUEUE,
    GPU_QUEUE,
    create_broker,
    extract_routing_key,
    get_broker,
    get_test_broker,
)
from .chain import (
    Pipeline,
    get_all_pipelines,
    get_pipeline,
    register_task,
    sync_pipeline_definitions,
)
from .exceptions import PipelineConfigError, PipelineError, PipelineStepError
from .message import PipelineMessage
from .middleware import DeadLetterMiddleware
from .worker import get_worker_queues, run_worker

__all__ = [
    "DEFAULT_QUEUE",
    "DICOM_QUEUE",
    "DLQ_QUEUE",
    "GPU_QUEUE",
    "DeadLetterMiddleware",
    "Pipeline",
    "PipelineConfigError",
    "PipelineError",
    "PipelineMessage",
    "PipelineStepError",
    "create_broker",
    "extract_routing_key",
    "get_all_pipelines",
    "get_broker",
    "get_pipeline",
    "get_test_broker",
    "get_worker_queues",
    "register_task",
    "run_worker",
    "sync_pipeline_definitions",
]
