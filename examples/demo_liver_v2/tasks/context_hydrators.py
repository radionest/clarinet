"""Custom slicer context hydrators for the liver study v2.

Loaded automatically at startup by ``load_custom_slicer_hydrators()``.
"""

from typing import Any

from clarinet.models.record import RecordRead
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
