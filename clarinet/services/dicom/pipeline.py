"""Built-in pipeline task for record-aware DICOM anonymization."""

from __future__ import annotations

from typing import Any

from clarinet.services.dicom.models import AnonymizationResult
from clarinet.services.dicom.orchestrator import create_anonymization_orchestrator
from clarinet.services.pipeline import PipelineMessage, TaskContext, pipeline_task
from clarinet.settings import settings


async def run_anonymization(
    msg: PipelineMessage,
    ctx: TaskContext,
    *,
    extra_record_data: dict[str, Any] | None = None,
) -> AnonymizationResult:
    """Run record-aware anonymization through the framework orchestrator.

    Helper for downstream pipeline tasks that need to add project-specific
    fields to the Record (e.g. ``study_type``) while delegating skip-guard,
    Patient anonymization, DICOM anonymization, and Record updates to the
    framework. Reuses ``ctx.client`` so no extra HTTP connection is opened.

    Reads ``send_to_pacs`` and ``save_to_disk`` from ``msg.payload``, falling
    back to the corresponding ``settings.anon_*`` defaults.

    Raises:
        ValueError: when ``msg.record_id`` is None — this helper is record-aware
            and must not silently degrade into an untracked anonymization.
    """
    if msg.record_id is None:
        raise ValueError(
            "run_anonymization requires PipelineMessage.record_id; "
            "use AnonymizationOrchestrator directly for record-less anonymization"
        )

    do_send = msg.payload.get("send_to_pacs", settings.anon_send_to_pacs)
    do_save = msg.payload.get("save_to_disk", settings.anon_save_to_disk)
    do_per_study = msg.payload.get("per_study_patient_id", settings.anon_per_study_patient_id)

    async with create_anonymization_orchestrator(client=ctx.client) as orch:
        return await orch.run(
            msg.study_uid,
            record_id=msg.record_id,
            save_to_disk=do_save,
            send_to_pacs=do_send,
            per_study_patient_id=do_per_study,
            extra_record_data=extra_record_data,
        )


@pipeline_task(queue=settings.dicom_queue_name)
async def anonymize_study_pipeline(msg: PipelineMessage, ctx: TaskContext) -> None:
    """Built-in DICOM anonymization with Record-aware bookkeeping.

    Use directly via RecordFlow
    ``do_task(anonymize_study_pipeline, send_to_pacs=True)``, or wrap in a
    project-specific task that calls :func:`run_anonymization` with
    ``extra_record_data`` to add project fields.
    """
    await run_anonymization(msg, ctx)
