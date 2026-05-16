"""Format Slicer script arguments from record metadata.

Renders ``record_type.slicer_script_args`` / ``slicer_result_validator_args``
templates against the record's working folder and identifier fields.

Strict by default: a non-anonymized record raises ``AnonPathError`` when
the working folder is computed via ``FileRepository``. UX callers that
need the legacy raw-UID fallback should keep using
``build_slicer_context`` — its layer-4/5 resolution path is independent
and preserves the fallback semantics intentionally.

The placeholder surface mirrors the legacy
``RecordRead._format_slicer_kwargs`` building block this module replaces
so existing user-defined Slicer arg templates keep rendering byte-for-byte
identically (when the record is anonymized).
"""

from typing import TYPE_CHECKING

from clarinet.models.base import DicomQueryLevel
from clarinet.settings import settings
from clarinet.utils.anon_resolve import require_anon_or_raw
from clarinet.utils.logger import logger

if TYPE_CHECKING:
    from clarinet.models.record import RecordRead
    from clarinet.types import SlicerArgs


__all__ = ["render_slicer_args"]


def _build_format_vars(record: "RecordRead", working_folder: str) -> dict[str, object]:
    """Build the placeholder map for a strict slicer-args render.

    Anon identifiers (``patient_id``, ``study_anon_uid``,
    ``series_anon_uid``) are resolved through ``require_anon_or_raw`` in
    strict mode — missing anonymization on a record whose template
    references those placeholders raises ``AnonPathError`` at call time.
    """
    patient_id = require_anon_or_raw(
        anon=record.patient.anon_id,
        raw=record.patient_id,
        level=DicomQueryLevel.PATIENT,
        fallback_to_unanonymized=False,
    )
    study_anon_uid: str | None = None
    if record.study_uid is not None:
        anon = record.study.anon_uid if record.study else record.study_anon_uid
        study_anon_uid = require_anon_or_raw(
            anon=anon,
            raw=record.study_uid,
            level=DicomQueryLevel.STUDY,
            fallback_to_unanonymized=False,
        )
    series_anon_uid: str | None = None
    if record.series_uid is not None:
        anon = record.series.anon_uid if record.series else record.series_anon_uid
        series_anon_uid = require_anon_or_raw(
            anon=anon,
            raw=record.series_uid,
            level=DicomQueryLevel.SERIES,
            fallback_to_unanonymized=False,
        )
    return {
        "working_folder": working_folder,
        "patient_id": patient_id,
        "patient_anon_name": record.patient.anon_name,
        "study_uid": record.study_uid,
        "study_anon_uid": study_anon_uid,
        "series_uid": record.series_uid,
        "series_anon_uid": series_anon_uid,
        "user_id": record.user_id,
        "clarinet_storage_path": record.clarinet_storage_path or settings.storage_path,
    }


def render_slicer_args(record: "RecordRead", *, validator: bool = False) -> "SlicerArgs | None":
    """Render slicer-arg templates from ``record_type``.

    Args:
        record: Fully-loaded record (patient/study/series eager-loaded).
        validator: ``True`` reads ``record_type.slicer_result_validator_args``;
            ``False`` (default) reads ``record_type.slicer_script_args``.

    Returns:
        Dict of rendered args, or ``None`` when the source field is unset
        on the record type. Templates that fail to resolve (unknown
        placeholder) are logged and skipped — matching the legacy
        ``_format_slicer_kwargs`` warn-and-skip behaviour.

    Raises:
        AnonPathError: When ``FileRepository`` cannot compute the working
            folder in strict mode (template needs ``{anon_*}`` but the
            record is not anonymized).
    """
    from clarinet.repositories.file_repository import FileRepository

    source = (
        record.record_type.slicer_result_validator_args
        if validator
        else record.record_type.slicer_script_args
    )
    if source is None:
        return None

    working_folder = str(FileRepository(record).working_dir)
    format_vars = _build_format_vars(record, working_folder)
    result: dict[str, str] = {}
    for key, template in source.items():
        try:
            result[key] = template.format(**format_vars)
        except (KeyError, AttributeError) as exc:
            logger.warning(f"Slicer arg '{key}': unresolved template '{template}' — {exc}")
    return result
