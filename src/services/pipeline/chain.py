"""
Pipeline chain builder DSL.

Provides the Pipeline class for defining multi-step task chains
with queue routing. Pipeline definitions are serialized into task
labels so the PipelineChainMiddleware can advance the chain.

Example:
    from src.services.pipeline import Pipeline

    imaging_pipeline = (
        Pipeline("ct_segmentation")
        .step(fetch_dicom, queue="clarinet.dicom")
        .step(run_segmentation, queue="clarinet.gpu")
        .step(generate_report, queue="clarinet.default")
    )

    # Execute:
    await imaging_pipeline.run(message)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from src.utils.logger import logger

from .broker import DEFAULT_QUEUE, extract_routing_key
from .message import PipelineMessage

if TYPE_CHECKING:
    from taskiq import AsyncTaskiqDecoratedTask

# Global registry: task_name -> decorated task function
_TASK_REGISTRY: dict[str, AsyncTaskiqDecoratedTask[..., Any]] = {}

# Global registry: pipeline_name -> Pipeline instance
_PIPELINE_REGISTRY: dict[str, Pipeline] = {}


class PipelineStep:
    """A single step in a pipeline chain.

    Args:
        task: The TaskIQ decorated task function.
        queue: Target queue name for this step.
    """

    def __init__(self, task: AsyncTaskiqDecoratedTask[..., Any], queue: str = DEFAULT_QUEUE):
        self.task = task
        self.queue = queue
        self.task_name = task.task_name

    def to_dict(self) -> dict[str, str]:
        """Serialize step to a dict for label storage.

        Returns:
            Dict with task_name and queue.
        """
        return {"task_name": self.task_name, "queue": self.queue}


class Pipeline:
    """Declarative pipeline chain builder.

    Defines an ordered sequence of task steps with queue routing.
    The chain definition is serialized into the first task's labels
    so the PipelineChainMiddleware can advance through steps.

    Args:
        name: Unique pipeline identifier.

    Example:
        pipeline = (
            Pipeline("ct_segmentation")
            .step(fetch_dicom, queue="clarinet.dicom")
            .step(run_segmentation, queue="clarinet.gpu")
            .step(generate_report)
        )
    """

    def __init__(self, name: str):
        self.name = name
        self.steps: list[PipelineStep] = []
        _PIPELINE_REGISTRY[name] = self

    def step(
        self,
        task: AsyncTaskiqDecoratedTask[..., Any],
        queue: str = DEFAULT_QUEUE,
    ) -> Pipeline:
        """Add a step to the pipeline.

        Args:
            task: The TaskIQ decorated task function.
            queue: Target queue name (default: ``clarinet.default``).

        Returns:
            Self for method chaining.
        """
        pipeline_step = PipelineStep(task=task, queue=queue)
        self.steps.append(pipeline_step)

        # Register the task in the global task registry
        if task.task_name not in _TASK_REGISTRY:
            _TASK_REGISTRY[task.task_name] = task

        return self

    def _serialize(self) -> str:
        """Serialize pipeline chain definition to JSON.

        Returns:
            JSON string with pipeline_id and steps list.
        """
        chain = {
            "pipeline_id": self.name,
            "steps": [s.to_dict() for s in self.steps],
        }
        return json.dumps(chain)

    async def run(self, message: PipelineMessage, **extra_labels: str) -> Any:
        """Execute the pipeline by dispatching the first step.

        The chain definition is attached as labels so the middleware
        can advance through subsequent steps automatically.

        Args:
            message: The initial pipeline message.
            **extra_labels: Additional labels to attach to the first task.

        Returns:
            The TaskIQ task handle for the first step.

        Raises:
            ValueError: If the pipeline has no steps.
        """
        if not self.steps:
            raise ValueError(f"Pipeline '{self.name}' has no steps")

        first_step = self.steps[0]
        routing_key = extract_routing_key(first_step.queue)

        labels = {
            "pipeline_id": self.name,
            "step_index": "0",
            "pipeline_chain": self._serialize(),
            "routing_key": routing_key,
            **extra_labels,
        }

        # Update message with pipeline context
        message = message.model_copy(update={"pipeline_id": self.name, "step_index": 0})

        logger.info(
            f"Starting pipeline '{self.name}' with {len(self.steps)} steps "
            f"(first step: '{first_step.task_name}' on queue '{first_step.queue}')"
        )

        return await first_step.task.kicker().with_labels(**labels).kiq(message.model_dump())

    def __repr__(self) -> str:
        step_names = [s.task_name for s in self.steps]
        return f"Pipeline('{self.name}', steps={step_names})"


def register_task(task: AsyncTaskiqDecoratedTask[..., Any]) -> None:
    """Register a task in the global task registry.

    Called automatically when tasks are added to pipelines via ``.step()``.
    Can also be called explicitly for standalone tasks.

    Args:
        task: The TaskIQ decorated task function.
    """
    _TASK_REGISTRY[task.task_name] = task


def get_pipeline(name: str) -> Pipeline | None:
    """Look up a registered pipeline by name.

    Args:
        name: The pipeline name.

    Returns:
        The Pipeline instance, or None if not found.
    """
    return _PIPELINE_REGISTRY.get(name)


def get_all_pipelines() -> dict[str, Pipeline]:
    """Get all registered pipelines.

    Returns:
        Dictionary mapping pipeline names to Pipeline instances.
    """
    return dict(_PIPELINE_REGISTRY)
