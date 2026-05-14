"""Resolver for configurable on-disk path templates.

The template ``settings.disk_path_template`` consists of exactly three
``/``-separated segments mapped to DICOM hierarchy levels::

    "<patient_segment>/<study_segment>/<series_segment>"

Resolver builds the working folder for any level (PATIENT/STUDY/SERIES)
by appending the corresponding number of segments to ``storage_path``.
Anonymized DICOM files (``dcm_anon/``) live as a sub-directory of the
SERIES-level working folder, so both ``AnonymizationService`` (writer)
and ``DicomWebCache`` (reader) compute the same path from this template.

Supported placeholders are listed in ``SUPPORTED_PLACEHOLDERS``. A given
placeholder resolves to ``"unknown"`` when the underlying entity field
is missing, so reader-side lookups remain non-fatal on incomplete data.

The resolver is pure-sync — safe to call from Pydantic ``computed_field``
properties (``SeriesRead.working_folder``, ``RecordRead.working_folder``).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import ConfigurationError
from clarinet.models.base import DicomQueryLevel
from clarinet.services.dicom.models import MODALITIES_SEPARATOR
from clarinet.settings import settings
from clarinet.utils.path_template import (
    SUPPORTED_PLACEHOLDERS,
    StrictDict,
    validate_template,
)

if TYPE_CHECKING:
    from clarinet.models.patient import Patient, PatientInfo
    from clarinet.models.study import Series, SeriesBase, Study, StudyBase


__all__ = [
    "SUPPORTED_PLACEHOLDERS",
    "AnonPathError",
    "TemplateSegments",
    "build_context",
    "derive_anon_patient_id",
    "render_working_folder",
    "split_template",
    "validate_template",
]


class AnonPathError(ConfigurationError):
    """Raised when a disk path template cannot be safely resolved."""


@dataclass(frozen=True, slots=True)
class TemplateSegments:
    """Three pieces of the disk path template."""

    patient: str
    study: str
    series: str


def split_template(template: str) -> TemplateSegments:
    """Split a 3-segment ``/``-separated template into its parts.

    Raises:
        AnonPathError: when the template has anything other than three
            non-empty segments.
    """
    parts = template.split("/")
    if len(parts) != 3 or any(not p.strip() for p in parts):
        raise AnonPathError(
            f"disk path template must contain exactly 3 non-empty "
            f"'/'-separated segments, got {len(parts)}: {template!r}"
        )
    return TemplateSegments(parts[0], parts[1], parts[2])


def _modalities_string(study: "Study | StudyBase | None") -> str:
    """Canonical join of a study's modalities (sorted, separator ``_``).

    Reads ``study.modalities_in_study`` (a ``MODALITIES_SEPARATOR``-joined
    string written by ``operations._ds_modalities``). Returns ``"unknown"``
    when missing — does NOT lazy-load ``study.series`` because callers
    reach this from ``computed_field`` properties on ``*Read`` DTOs
    where the relationship may not be eagerly loaded; lazy-load on an
    async session raises ``MissingGreenlet``.
    """
    if study is None:
        return "unknown"
    raw = getattr(study, "modalities_in_study", None)
    if raw:
        parts = sorted({p.strip() for p in raw.split(MODALITIES_SEPARATOR) if p.strip()})
        if parts:
            return "_".join(parts)
    return "unknown"


def derive_anon_patient_id(
    patient: "Patient | PatientInfo | None",
    study: "Study | StudyBase | None",
) -> str:
    """Derive the anonymized patient identifier for the current run mode.

    In per-study mode (``settings.anon_per_study_patient_id``), the value
    is a deterministic per-study hash so different studies of the same
    patient land in different folders / DICOM tags. Otherwise it is the
    per-patient ``anon_id`` (``f"{prefix}_{auto_id}"``).

    Falls back to ``patient.id`` (raw PatientID) when ``anon_id`` is not
    available — preserves legacy working_folder behavior for records
    that haven't been anonymized yet. Returns ``"unknown"`` only when
    no patient is supplied at all.
    """
    from clarinet.services.dicom.anonymizer import compute_per_study_patient_id

    if settings.anon_per_study_patient_id:
        study_uid = getattr(study, "study_uid", None) if study else None
        if study_uid:
            return compute_per_study_patient_id(
                settings.anon_uid_salt,
                study_uid,
                settings.anon_per_study_patient_id_hex_length,
                prefix=settings.anon_id_prefix,
            )
    anon = getattr(patient, "anon_id", None) if patient else None
    if anon:
        return str(anon)
    raw = getattr(patient, "id", None) if patient else None
    return str(raw) if raw else "unknown"


def build_context(
    *,
    patient: "Patient | PatientInfo | None",
    study: "Study | StudyBase | None",
    series: "Series | SeriesBase | None",
    anon_patient_id: str | None = None,
    anon_study_uid: str | None = None,
    anon_series_uid: str | None = None,
) -> dict[str, str]:
    """Build the placeholder dict for template rendering.

    ``anon_*`` kwargs let the writer pass the exact values it is about
    to embed in the DICOM tags (so the path matches the tags even when
    settings changed between runs). When omitted, the resolver derives
    them from DB state (``anon_id``, ``anon_uid``) or falls back to the
    underlying original UID / ``"unknown"``.

    All values are returned as ``str`` so ``str.format`` can interpolate
    them directly.
    """
    pid_resolved = anon_patient_id or derive_anon_patient_id(patient, study)

    if anon_study_uid:
        study_resolved = anon_study_uid
    else:
        study_resolved = (
            (study.anon_uid if study and study.anon_uid else None)
            or (study.study_uid if study else None)
            or "unknown"
        )

    if anon_series_uid:
        series_resolved = anon_series_uid
    else:
        series_resolved = (
            (series.anon_uid if series and series.anon_uid else None)
            or (series.series_uid if series else None)
            or "unknown"
        )

    return {
        "anon_patient_id": pid_resolved,
        "anon_study_uid": study_resolved,
        "anon_series_uid": series_resolved,
        "patient_id": (patient.id if patient else None) or "unknown",
        "patient_auto_id": (
            str(patient.auto_id) if patient and patient.auto_id is not None else "unknown"
        ),
        "anon_id_prefix": settings.anon_id_prefix or "anon",
        "study_uid": (study.study_uid if study else None) or "unknown",
        "series_uid": (series.series_uid if series else None) or "unknown",
        "study_date": (
            study.date.strftime("%Y%m%d") if study and getattr(study, "date", None) else "unknown"
        ),
        "study_modalities": _modalities_string(study),
        "series_modality": (series.modality if series and series.modality else "unknown"),
    }


def _safe_render(segment: str, context: dict[str, str]) -> str:
    """Render a single template segment with context.

    Validates the result is a single non-empty directory name without
    embedded path separators or traversal tokens.
    """
    try:
        out = segment.format_map(StrictDict(context))
    except KeyError as exc:
        raise AnonPathError(f"unknown placeholder {exc.args[0]!r} in segment {segment!r}") from exc
    if not out or "/" in out or "\\" in out or out in (".", "..") or out.startswith("."):
        raise AnonPathError(f"unsafe rendered segment {out!r} (from template {segment!r})")
    return out


def render_working_folder(
    template: str,
    level: DicomQueryLevel,
    context: dict[str, str],
    storage_path: Path,
) -> Path:
    """Resolve the working folder for a record of the given DICOM level.

    PATIENT -> ``storage_path / patient_segment``
    STUDY   -> ``storage_path / patient_segment / study_segment``
    SERIES  -> ``storage_path / patient_segment / study_segment / series_segment``
    """
    segs = split_template(template)
    patient_dir = _safe_render(segs.patient, context)
    if level is DicomQueryLevel.PATIENT:
        return storage_path / patient_dir
    study_dir = _safe_render(segs.study, context)
    if level is DicomQueryLevel.STUDY:
        return storage_path / patient_dir / study_dir
    series_dir = _safe_render(segs.series, context)
    return storage_path / patient_dir / study_dir / series_dir
