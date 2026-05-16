"""Resolver for configurable on-disk path templates.

The template ``settings.disk_path_template`` consists of exactly three
``/``-separated segments mapped to DICOM hierarchy levels::

    "<patient_segment>/<study_segment>/<series_segment>"

Resolver builds the working folder for any level (PATIENT/STUDY/SERIES)
by appending the corresponding number of segments to ``storage_path``.
Anonymized DICOM files (``dcm_anon/``) live as a sub-directory of the
SERIES-level working folder, so both ``AnonymizationService`` (writer)
and ``DicomWebCache`` (reader) compute the same path from this template.
All non-writer call sites (pipeline ``FileResolver``, Slicer context,
file validation, ``FileRepository``) reach the same path through
``render_all_levels`` — the single rendering point for storage
directories.

Supported placeholders are listed in ``SUPPORTED_PLACEHOLDERS``. Backend
callers run with the default ``fallback_to_unanonymized=False`` and get
``AnonPathError`` when an entity is not anonymized yet — surfaces the
asymmetric-anonymization race instead of silently rendering a path
against raw UIDs. UX callers pass ``fallback_to_unanonymized=True`` to
fall back to raw UIDs / ``"unknown"`` (legacy non-fatal behavior).

The resolver is pure-sync — safe to call from Pydantic helper methods
and from non-async backend code paths.

Lives in ``services/common`` because the same template engine is used
by DICOM anonymization, ``FileRepository`` (the sole entry point for
path resolution in services and routers), pipeline file resolution and
Slicer context — semantically about storage paths, not about DICOM
anonymization. The DICOM-anon-specific helper ``derive_anon_patient_id``
is co-located because the same per-study / per-patient ID derivation
feeds writer, reader, and UX placeholder rendering — keeping them
apart would let writer and reader disagree on the directory name.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clarinet.exceptions.domain import AnonPathError
from clarinet.models.base import DicomQueryLevel
from clarinet.services.dicom.models import MODALITIES_SEPARATOR
from clarinet.settings import settings
from clarinet.utils.anon_resolve import require_anon_or_raw
from clarinet.utils.path_template import (
    SUPPORTED_PLACEHOLDERS,
    StrictDict,
    extract_placeholders,
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
    "render_all_levels",
    "render_working_folder",
    "split_template",
    "validate_template",
]


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
    reach this from ``FileRepository`` / ``FileResolver`` where the
    relationship may not be eagerly loaded; lazy-load on an async session
    raises ``MissingGreenlet``.
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
    *,
    fallback_to_unanonymized: bool = False,
) -> str:
    """Derive the anonymized patient identifier for the current run mode.

    In per-study mode (``settings.anon_per_study_patient_id``), the value
    is a deterministic per-study hash so different studies of the same
    patient land in different folders / DICOM tags. Otherwise it is the
    per-patient ``anon_id`` (``f"{prefix}_{auto_id}"``).

    Backend code (file paths, PACS lookups) must run with the default
    ``fallback_to_unanonymized=False`` — raises ``AnonPathError`` when
    anonymized identifiers are missing, surfacing the asymmetric
    anonymization race instead of silently returning a non-anonymized
    path. UX call sites (viewer URIs, Slicer template vars) should pass
    ``fallback_to_unanonymized=True`` to preserve the legacy behavior of
    falling back to ``patient.id`` (or ``"unknown"`` when no patient).
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
    if patient is None:
        # Caller did not supply a patient — keep the "unknown" sentinel so
        # PATIENT-level template rendering works without forcing the caller
        # to load a Patient just to render a study/series segment.
        return "unknown"
    raw_id = getattr(patient, "id", None)
    return require_anon_or_raw(
        anon=getattr(patient, "anon_id", None),
        raw=str(raw_id) if raw_id else None,
        level=DicomQueryLevel.PATIENT,
        fallback_to_unanonymized=fallback_to_unanonymized,
    )


def build_context(
    *,
    patient: "Patient | PatientInfo | None",
    study: "Study | StudyBase | None",
    series: "Series | SeriesBase | None",
    template: str | None = None,
    anon_patient_id: str | None = None,
    anon_study_uid: str | None = None,
    anon_series_uid: str | None = None,
    fallback_to_unanonymized: bool = False,
) -> dict[str, str]:
    """Build the placeholder dict for template rendering.

    Only the placeholders that ``template`` actually references are
    resolved — a raw-UID template (no ``{anon_*}``) never triggers
    anonymized-UID resolution, so missing ``anon_uid`` does not raise.
    When ``template`` is None, falls back to ``settings.disk_path_template``.

    ``anon_*`` kwargs let the writer pass the exact values it is about
    to embed in the DICOM tags (so the path matches the tags even when
    settings changed between runs). When omitted, the resolver derives
    them from DB state (``anon_id``, ``anon_uid``).

    ``anon_*`` override kwargs are honored only when the corresponding
    placeholder appears in ``template``. For a raw-UID template like
    ``"{patient_id}/{study_uid}/{series_uid}"``, an ``anon_patient_id="X"``
    argument is silently dropped — the rendered path follows the template,
    not the override. Callers that need anonymized paths must use a template
    that references ``{anon_*}``.

    Backend callers (file paths, dcm_anon lookups, anonymization writer)
    keep the default ``fallback_to_unanonymized=False`` — missing
    ``anon_uid`` then raises ``AnonPathError`` instead of silently
    rendering a non-anonymized path. UX callers (viewer URIs, Slicer
    template vars) pass ``fallback_to_unanonymized=True`` to fall back
    to the raw UID / ``"unknown"`` so the UI keeps working on records
    that have not been anonymized yet.

    All values are returned as ``str`` so ``str.format`` can interpolate
    them directly.
    """
    tmpl = template if template is not None else settings.disk_path_template
    needed = extract_placeholders(tmpl)
    ctx: dict[str, str] = {}

    if "anon_patient_id" in needed:
        ctx["anon_patient_id"] = anon_patient_id or derive_anon_patient_id(
            patient, study, fallback_to_unanonymized=fallback_to_unanonymized
        )
    if "anon_study_uid" in needed:
        if anon_study_uid:
            ctx["anon_study_uid"] = anon_study_uid
        elif study is None:
            # Caller did not supply a study — sentinel so PATIENT-level template
            # rendering succeeds (it will not reference {anon_study_uid} anyway).
            ctx["anon_study_uid"] = "unknown"
        else:
            ctx["anon_study_uid"] = require_anon_or_raw(
                anon=study.anon_uid,
                raw=study.study_uid,
                level=DicomQueryLevel.STUDY,
                fallback_to_unanonymized=fallback_to_unanonymized,
            )
    if "anon_series_uid" in needed:
        if anon_series_uid:
            ctx["anon_series_uid"] = anon_series_uid
        elif series is None:
            ctx["anon_series_uid"] = "unknown"
        else:
            ctx["anon_series_uid"] = require_anon_or_raw(
                anon=series.anon_uid,
                raw=series.series_uid,
                level=DicomQueryLevel.SERIES,
                fallback_to_unanonymized=fallback_to_unanonymized,
            )
    if "patient_id" in needed:
        ctx["patient_id"] = (patient.id if patient else None) or "unknown"
    if "patient_auto_id" in needed:
        ctx["patient_auto_id"] = (
            str(patient.auto_id) if patient and patient.auto_id is not None else "unknown"
        )
    if "anon_id_prefix" in needed:
        ctx["anon_id_prefix"] = settings.anon_id_prefix or "anon"
    if "study_uid" in needed:
        ctx["study_uid"] = (study.study_uid if study else None) or "unknown"
    if "series_uid" in needed:
        ctx["series_uid"] = (series.series_uid if series else None) or "unknown"
    if "study_date" in needed:
        ctx["study_date"] = (
            study.date.strftime("%Y%m%d") if study and getattr(study, "date", None) else "unknown"
        )
    if "study_modalities" in needed:
        ctx["study_modalities"] = _modalities_string(study)
    if "series_modality" in needed:
        ctx["series_modality"] = series.modality if series and series.modality else "unknown"
    if "series_num" in needed:
        ctx["series_num"] = (
            f"{series.series_number:05d}"
            if series and getattr(series, "series_number", None) is not None
            else "unknown"
        )

    return ctx


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


def render_all_levels(
    *,
    patient: "Patient | PatientInfo | None",
    study: "Study | StudyBase | None",
    series: "Series | SeriesBase | None",
    storage_path: Path,
    template: str | None = None,
    fallback_to_unanonymized: bool = False,
    anon_patient_id: str | None = None,
    anon_study_uid: str | None = None,
    anon_series_uid: str | None = None,
) -> dict[DicomQueryLevel, Path]:
    """Render PATIENT / STUDY / SERIES dirs from ``disk_path_template``.

    Returns only those levels for which the corresponding entity is
    present:

    - ``patient`` → ``{PATIENT}``
    - ``patient`` + ``study`` → ``{PATIENT, STUDY}``
    - ``patient`` + ``study`` + ``series`` → ``{PATIENT, STUDY, SERIES}``

    A ``None`` ``patient`` returns an empty mapping — without a patient
    there is no anchor for the PATIENT segment. To render only a
    deeper level (e.g. just SERIES) supply both ``patient`` and
    ``study`` plus the override kwargs as needed.

    ``template`` defaults to ``settings.disk_path_template``.

    ``anon_*`` override kwargs are forwarded to ``build_context`` — used
    by writer paths that need to embed the values they are about to
    write into the DICOM tags (race-safety against DB-update lag).

    Raises:
        AnonPathError: when an anonymized identifier is missing and
            ``fallback_to_unanonymized`` is False, or when the rendered
            template contains an unsafe path segment.
    """
    if patient is None:
        return {}

    tmpl = template if template is not None else settings.disk_path_template
    ctx = build_context(
        patient=patient,
        study=study,
        series=series,
        template=tmpl,
        anon_patient_id=anon_patient_id,
        anon_study_uid=anon_study_uid,
        anon_series_uid=anon_series_uid,
        fallback_to_unanonymized=fallback_to_unanonymized,
    )

    dirs: dict[DicomQueryLevel, Path] = {
        DicomQueryLevel.PATIENT: render_working_folder(
            tmpl, DicomQueryLevel.PATIENT, ctx, storage_path
        )
    }
    if study is None:
        return dirs
    dirs[DicomQueryLevel.STUDY] = render_working_folder(
        tmpl, DicomQueryLevel.STUDY, ctx, storage_path
    )
    if series is None:
        return dirs
    dirs[DicomQueryLevel.SERIES] = render_working_folder(
        tmpl, DicomQueryLevel.SERIES, ctx, storage_path
    )
    return dirs
