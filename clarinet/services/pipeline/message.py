"""
Pydantic models for pipeline messages.

PipelineMessage carries context through pipeline steps.
"""

from typing import Any

from pydantic import BaseModel, Field


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
