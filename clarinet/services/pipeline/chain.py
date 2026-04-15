"""
Pipeline chain builder DSL.

Provides the Pipeline class for defining multi-step task chains
with queue routing. Pipeline definitions are stored in the database
and fetched by workers via the HTTP API at each chain step.

Example:
    from clarinet.services.pipeline import Pipeline

    imaging_pipeline = (
        Pipeline("ct_segmentation")
        .step(fetch_dicom, queue="clarinet.dicom")
        .step(run_segmentation, queue="clarinet.gpu")
        .step(generate_report, queue="clarinet.default")
    )

    # Execute (dispatches first step):
    await imaging_pipeline.run(message)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from clarinet.exceptions.domain import PipelineConfigError
from clarinet.utils.logger import logger

from .broker import DEFAULT_QUEUE, extract_routing_key
from .message import PipelineMessage

if TYPE_CHECKING:
    from taskiq import AsyncTaskiqDecoratedTask

    from clarinet.repositories.pipeline_definition_repository import PipelineDefinitionRepository

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
    Pipeline definitions are synced to the database at application startup
    (and on demand via ``POST /api/pipelines/sync``). Workers fetch them
    via the HTTP API at each chain step.

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
        register_task(task)

        return self

    async def run(self, message: PipelineMessage, **extra_labels: str) -> Any:
        """Execute the pipeline by dispatching the first step.

        Pipeline definitions must be synced to the database beforehand
        (at startup or via ``POST /api/pipelines/sync``).

        Args:
            message: The initial pipeline message.
            **extra_labels: Additional labels to attach to the first task.

        Returns:
            The TaskIQ task handle for the first step.

        Raises:
            PipelineConfigError: If the pipeline has no steps.
        """
        if not self.steps:
            raise PipelineConfigError(f"Pipeline '{self.name}' has no steps")

        first_step = self.steps[0]
        routing_key = extract_routing_key(first_step.queue)

        labels = {
            "pipeline_id": self.name,
            "step_index": "0",
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

    Raises:
        PipelineConfigError: If a different task with the same name is already
            registered (prevents project tasks from shadowing built-in tasks).

    Args:
        task: The TaskIQ decorated task function.
    """
    if ":" not in task.task_name:
        logger.warning(
            f"Task '{task.task_name}' registered without namespace prefix. "
            f"Use @pipeline_task() or explicit task_name='namespace:name'."
        )
    existing = _TASK_REGISTRY.get(task.task_name)
    if existing is not None and existing is not task:
        raise PipelineConfigError(
            f"Task name collision: '{task.task_name}' is already registered "
            f"by a different task object. Each task must have a unique name."
        )
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


async def persist_definitions(repo: PipelineDefinitionRepository) -> int:
    """Persist all registered pipeline definitions to the database.

    Args:
        repo: Pipeline definition repository instance.

    Returns:
        Number of persisted definitions.
    """
    for pipeline in _PIPELINE_REGISTRY.values():
        await repo.upsert(pipeline.name, [s.to_dict() for s in pipeline.steps])
    return len(_PIPELINE_REGISTRY)


async def sync_pipeline_definitions() -> int:
    """Sync all registered pipeline definitions to the database.

    Bootstrap variant — creates its own session via db_manager.
    Called at application startup.

    Returns:
        Number of synced definitions.
    """
    from clarinet.repositories.pipeline_definition_repository import PipelineDefinitionRepository
    from clarinet.utils.db_manager import db_manager

    async with db_manager.get_async_session_context() as session:
        return await persist_definitions(PipelineDefinitionRepository(session))
