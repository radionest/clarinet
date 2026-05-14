"""Custom slicer context hydrators for the liver study v2.

Loaded automatically at startup by ``load_custom_slicer_hydrators()``.

Hydrator outputs flow into Slicer scripts that issue PACS C-GET/C-MOVE
calls and address files on disk, so they MUST use anonymized UIDs.
When an entity has not been anonymized yet, the hydrator skips it
(``return {}``) and logs a warning — silent fallback to raw UIDs would
make the script load the wrong dataset.
"""

import os
from typing import Any

from clarinet.models.record import RecordRead
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.slicer.context_hydration import (
    SlicerHydrationContext,
    slicer_context_hydrator,
)
from clarinet.utils.logger import logger


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
    if not first.anon_uid:
        logger.warning(
            f"patient_first_study: skipping — study {first.study_uid} not anonymized yet"
        )
        return {}
    return {"best_study_uid": first.anon_uid}


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
        record_type_name="first-check",
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
            if not series.anon_uid:
                logger.warning(
                    f"best_series_from_first_check: skipping — series "
                    f"{series.series_uid} not anonymized yet"
                )
                return {}
            return {"best_series_uid": series.anon_uid}

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
        record_type_name="first-check",
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
    if not study.anon_uid:
        logger.warning(
            f"model_series_for_projection: skipping — study {study.study_uid} not anonymized yet"
        )
        return {}

    for series in study.series:
        if series.series_uid == best_series_original:
            if not series.anon_uid:
                logger.warning(
                    f"model_series_for_projection: skipping — series "
                    f"{series.series_uid} not anonymized yet"
                )
                return {}
            return {
                "model_study_uid": study.anon_uid,
                "model_series_uid": series.anon_uid,
            }

    return {}


@slicer_context_hydrator("projection_for_update")
async def hydrate_projection_for_update(
    record: RecordRead,
    context: dict[str, Any],
    ctx: SlicerHydrationContext,
) -> dict[str, Any]:
    """Inject projection and doctor segmentation paths for master model update.

    Finds a ``compare_with_projection`` record with false positives for this
    patient, resolves the target study/series UIDs, and builds file paths for
    the projection and doctor segmentation so the Slicer script can compute
    NEW_* false-positive ROIs at runtime.

    Returns:
        Dict with ``target_study_uid``, ``target_series_uid``,
        ``projection_path``, ``doctor_segmentation_path``;
        or empty dict if no comparison with false positives exists
        (graceful fallback for intraop trigger).
    """
    criteria = RecordSearchCriteria(
        patient_id=record.patient_id,
        record_type_name="compare-with-projection",
    )
    comparisons = await ctx.record_repo.find_by_criteria(criteria)

    # Find first comparison with false positives
    comp = None
    for c in comparisons:
        if (c.data or {}).get("false_positive_num", 0) > 0:
            comp = c
            break

    if comp is None:
        return {}

    # Get doctor user_id from parent segmentation record
    if comp.parent_record_id is None:
        return {}
    parent_record = await ctx.record_repo.get(comp.parent_record_id)
    user_id = parent_record.user_id
    if user_id is None:
        return {}

    # Resolve study and series to anonymized UIDs
    if comp.study_uid is None:
        return {}
    study = await ctx.study_repo.get_with_series(comp.study_uid)
    if not study.anon_uid:
        logger.warning(
            f"projection_for_update: skipping — study {study.study_uid} not anonymized yet"
        )
        return {}
    study_anon_uid = study.anon_uid

    # Find the target series
    target_series_uid: str | None = None
    if comp.series_uid:
        for series in study.series:
            if series.series_uid == comp.series_uid:
                if not series.anon_uid:
                    logger.warning(
                        f"projection_for_update: skipping — series "
                        f"{series.series_uid} not anonymized yet"
                    )
                    return {}
                target_series_uid = series.anon_uid
                break

    if target_series_uid is None:
        return {}

    # Build file paths following FileResolver.build_working_dirs convention
    patient_dir = context.get("working_folder", "")
    projection_path = os.path.join(
        patient_dir,
        study_anon_uid,
        target_series_uid,
        "master_projection.seg.nrrd",
    )
    parent_type = parent_record.record_type_name  # FK field, always available
    doctor_segmentation_path = os.path.join(
        patient_dir,
        study_anon_uid,
        f"segmentation_{parent_type}_{user_id}.seg.nrrd",
    )

    return {
        "target_study_uid": study_anon_uid,
        "target_series_uid": target_series_uid,
        "projection_path": projection_path,
        "doctor_segmentation_path": doctor_segmentation_path,
    }
