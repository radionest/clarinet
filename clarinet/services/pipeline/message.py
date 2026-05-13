"""
Pydantic models for pipeline messages.

PipelineMessage carries context through pipeline steps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from clarinet.models import RecordRead


class PipelineMessage(BaseModel):
    """Message passed between pipeline steps.

    Carries patient/study/series context and arbitrary payload data.
    The pipeline_id and step_index are set by the chain middleware
    to track progress through a multi-step pipeline.

    Args:
        patient_id: Patient identifier.
        study_uid: DICOM Study Instance UID.
        series_uid: Optional DICOM Series Instance UID.
        record_id: Optional Clarinet record ID.
        record_type_name: Optional record type name.
        payload: Arbitrary key-value data for the step.
        pipeline_id: Pipeline name (set by chain middleware).
        step_index: Current step index in the pipeline (set by chain middleware).
    """

    patient_id: str
    study_uid: str
    series_uid: str | None = None
    record_id: int | None = None
    record_type_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    pipeline_id: str | None = None
    step_index: int = 0


def build_pipeline_message_from_record(
    record: RecordRead,
    *,
    payload: dict[str, Any] | None = None,
) -> PipelineMessage:
    """Build a :class:`PipelineMessage` from a hydrated :class:`RecordRead`.

    Mirrors the mapping used by ``RecordFlowEngine._dispatch_pipeline``: the
    patient / study / series identifiers and the record's type name are pulled
    off the record's relations. ``patient_id`` and ``study_uid`` fall back to
    empty strings because :class:`PipelineMessage` declares them as required
    ``str`` for backward compatibility with older tasks that assert presence.
    Callers that need stricter guarantees should validate the record before
    constructing the message.
    """
    return PipelineMessage(
        patient_id=record.patient_id or "",
        study_uid=record.study_uid or "",
        series_uid=record.series_uid,
        record_id=record.id,
        record_type_name=record.record_type.name if record.record_type else None,
        payload=dict(payload) if payload else {},
    )
