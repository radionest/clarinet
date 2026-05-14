"""Helper to resolve an anonymized identifier with optional raw-UID fallback.

Centralizes the "anon -> fallback -> raise" pattern used by storage path
rendering (``clarinet/services/common/storage_paths.py``), record-level
template formatting (``clarinet/models/record.py::RecordRead``), and
series-level template formatting (``clarinet/models/study.py::SeriesRead``).

Backend callers run with the default ``fallback_to_unanonymized=False``
to surface the asymmetric-anonymization race instead of silently
rendering a non-anonymized path. UX callers (viewer URIs, Slicer args,
admin endpoints) pass ``True`` to fall back to the raw UID — see
``services/dicom/CLAUDE.md`` for the full anonymization contract.
"""

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel

_LABELS: dict[DicomQueryLevel, tuple[str, str]] = {
    DicomQueryLevel.PATIENT: ("anon_id", "patient_id"),
    DicomQueryLevel.STUDY: ("anon_uid", "study_uid"),
    DicomQueryLevel.SERIES: ("anon_uid", "series_uid"),
}


def require_anon_or_raw(
    *,
    anon: str | None,
    raw: str | None,
    level: DicomQueryLevel,
    fallback_to_unanonymized: bool,
) -> str:
    """Return ``anon`` if present, else ``raw`` (when allowed), else raise.

    Args:
        anon: Anonymized identifier (``patient.anon_id`` / ``study.anon_uid``
            / ``series.anon_uid``). Falsy values trigger the fallback branch.
        raw: Raw identifier used when ``fallback_to_unanonymized`` is True.
            Must be truthy — empty string / None still raises (no silent
            empty path segment).
        level: DICOM hierarchy level — selects field labels in the error
            message ("Patient has no anon_id", "Study has no anon_uid",
            "Series has no anon_uid").
        fallback_to_unanonymized: UX call sites pass True to preserve the
            legacy non-fatal behavior; backend code keeps the default False.

    Raises:
        AnonPathError: ``anon`` is missing and either ``fallback_to_unanonymized``
            is False or ``raw`` itself is missing.
    """
    if anon:
        return anon
    if fallback_to_unanonymized and raw:
        return raw
    anon_label, raw_label = _LABELS[level]
    raise AnonPathError(
        f"{level.value.title()} has no {anon_label} ({raw_label}={raw!r}); "
        "pass fallback_to_unanonymized=True for UX call sites"
    )
