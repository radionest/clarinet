"""Unified execution context for RecordFlow actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clarinet.models import RecordRead


@dataclass(frozen=True, slots=True)
class FlowContext:
    """Unified execution context for all flow trigger types."""

    record: RecordRead | None = None
    record_context: dict[str, list[RecordRead]] | None = None
    patient_id: str | None = None
    study_uid: str | None = None
    series_uid: str | None = None
    file_name: str | None = None
    source_record: RecordRead | None = None

    @staticmethod
    def for_record(record: RecordRead, context: dict[str, list[RecordRead]]) -> FlowContext:
        """Build context for a record-triggered flow."""
        return FlowContext(
            record=record,
            record_context=context,
            patient_id=record.patient.id,
            study_uid=record.study.study_uid if record.study else None,
            series_uid=record.series.series_uid if record.series else None,
        )

    @staticmethod
    def for_entity(
        patient_id: str,
        study_uid: str | None = None,
        series_uid: str | None = None,
    ) -> FlowContext:
        """Build context for an entity-creation flow."""
        return FlowContext(patient_id=patient_id, study_uid=study_uid, series_uid=series_uid)

    @staticmethod
    def for_file(
        file_name: str,
        patient_id: str,
        source_record: RecordRead | None = None,
    ) -> FlowContext:
        """Build context for a file-update flow."""
        return FlowContext(
            file_name=file_name,
            patient_id=patient_id,
            source_record=source_record,
        )
