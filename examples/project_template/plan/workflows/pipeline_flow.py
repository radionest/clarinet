"""Pipeline flow for the project.

Two halves in this file:

1. Pipeline tasks — async/sync functions decorated with ``@pipeline_task``.
2. RecordFlow DSL — declarative event → action rules.

See `.claude/rules/workflows.md` for the full reference.
"""

from __future__ import annotations

from record_types import example_segmentation

from clarinet.services.pipeline import (
    PipelineMessage,
    SyncTaskContext,
    pipeline_task,
)
from clarinet.services.recordflow import Field, record, study
from clarinet.utils.logger import logger

F = Field()

# ---------------------------------------------------------------------------
# Pipeline tasks
# ---------------------------------------------------------------------------
# TODO: replace the example task with your real pipeline tasks.


@pipeline_task()
def example_post_segment(_msg: PipelineMessage, ctx: SyncTaskContext) -> None:
    """Example post-processing task — runs after a segmentation is finalised.

    Idempotency contract: a task must check if its output already exists and
    return early. Otherwise retries / cascade-invalidation will redo the work.
    """
    if not ctx.files.exists(example_segmentation):
        logger.info("Segmentation not yet present, skipping post-processing")
        return

    # TODO: implement your post-processing here (compute metrics, derive files, etc.).
    logger.info("Example post-segment task executed (no-op stub)")


# ---------------------------------------------------------------------------
# RecordFlow DSL
# ---------------------------------------------------------------------------
# TODO: replace the example flows with your real workflow.

# 1. Когда в проект приходит исследование — создаём первичный осмотр.
(study().on_creation().create_record("first-check"))

# 2. Если first-check одобрил study — создаём example-segment.
(record("first-check").on_finished().if_record(F.is_good == True).create_record("example-segment"))

# 3. После завершения сегментации — запускаем post-processing pipeline task.
(record("example-segment").on_finished().do_task(example_post_segment))
