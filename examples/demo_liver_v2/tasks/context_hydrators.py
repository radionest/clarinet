"""Custom slicer context hydrators for the liver study v2.

Loaded automatically at startup by ``load_custom_slicer_hydrators()``.
"""

from typing import Any

from clarinet.models.record import RecordRead
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.slicer.context_hydration import (
    SlicerHydrationContext,
    slicer_context_hydrator,
)


@slicer_context_hydrator("patient_first_study")
async def hydrate_patient_first_study(
    record: RecordRead,
    _context: dict[str, Any],
    ctx: SlicerHydrationContext,
) -> dict[str, Any]:
    """Inject ``best_study_uid`` for PATIENT-level records.

    Finds the earliest study for the patient and returns its anonymized UID
    so that Slicer scripts can load the volume from PACS.
    """
    studies = await ctx.study_repo.find_by_patient(record.patient_id)
    if not studies:
        return {}

    first = sorted(studies, key=lambda s: s.date or "")[0]
    return {"best_study_uid": first.anon_uid or first.study_uid}


@slicer_context_hydrator("best_series_from_first_check")
async def hydrate_best_series_from_first_check(
    record: RecordRead,
    _context: dict[str, Any],
    ctx: SlicerHydrationContext,
) -> dict[str, Any]:
    """Inject ``best_series_uid`` for segmentation records.

    Finds the ``first_check`` record for the same study, extracts the
    ``best_series`` UID from its data, translates it to the anonymized
    Series UID, and returns it so Slicer scripts can load only that
    series from PACS.

    Returns:
        Dict with ``best_series_uid`` (anon UID) or empty dict if not found.
    """
    criteria = RecordSearchCriteria(
        record_type_name="first_check",
        study_uid=record.study_uid,
    )
    first_check_records = await ctx.record_repo.find_by_criteria(criteria)

    if not first_check_records:
        return {}

    first_check = first_check_records[0]
    best_series_original = (first_check.data or {}).get("best_series")

    if not best_series_original:
        return {}

    # Translate original series UID to anon UID
    study = await ctx.study_repo.get_with_series(record.study_uid)

    for series in study.series:
        if series.series_uid == best_series_original:
            return {"best_series_uid": series.anon_uid or series.series_uid}

    return {}


@slicer_context_hydrator("model_series_for_projection")
async def hydrate_model_series_for_projection(
    record: RecordRead,
    _context: dict[str, Any],
    ctx: SlicerHydrationContext,
) -> dict[str, Any]:
    """Inject ``model_study_uid`` and ``model_series_uid`` for projection scripts.

    Finds the CT ``first_check`` record for the same patient, extracts the
    ``best_series`` UID, resolves both study and series to anonymized UIDs,
    and returns them so the Slicer script can load the reference CT volume.

    Returns:
        Dict with ``model_study_uid`` and ``model_series_uid`` (anon UIDs),
        or empty dict if CT first_check not found.
    """
    criteria = RecordSearchCriteria(
        patient_id=record.patient_id,
        record_type_name="first_check",
    )
    first_check_records = await ctx.record_repo.find_by_criteria(criteria)

    # Filter for CT study_type in Python
    ct_first_check = None
    for fc in first_check_records:
        if (fc.data or {}).get("study_type") == "CT":
            ct_first_check = fc
            break

    if ct_first_check is None:
        return {}

    best_series_original = (ct_first_check.data or {}).get("best_series")
    ct_study_uid = ct_first_check.study_uid
    if not best_series_original or not ct_study_uid:
        return {}

    # Resolve study and series to anonymized UIDs
    study = await ctx.study_repo.get_with_series(ct_study_uid)

    for series in study.series:
        if series.series_uid == best_series_original:
            return {
                "model_study_uid": study.anon_uid or study.study_uid,
                "model_series_uid": series.anon_uid or series.series_uid,
            }

    return {}
