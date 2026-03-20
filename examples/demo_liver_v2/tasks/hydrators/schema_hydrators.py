"""Custom schema hydrators for the liver study v2.

Loaded automatically at startup by ``load_custom_hydrators()``.
"""

from typing import Any

from clarinet.models.record import Record
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.schema_hydration import HydrationContext, schema_hydrator
from clarinet.utils.logger import logger


@schema_hydrator("resection_plan_lesions")
async def hydrate_resection_plan_lesions(
    record: Record,
    _options: dict[str, Any],
    ctx: HydrationContext,
) -> list[dict[str, Any]]:
    """Return lesion numbers from the patient's resection-plan as ``oneOf`` items.

    Reads the ``resection-plan`` record for the same patient and extracts
    ``lesions[*].lesion_num`` from its data.  The frontend uses this list
    to pre-build one form row per lesion, guaranteeing completeness.
    """
    if not record.patient_id:
        return []

    criteria = RecordSearchCriteria(
        patient_id=record.patient_id,
        record_type_name="resection-plan",
    )
    plans = await ctx.record_repo.find_by_criteria(criteria)

    if not plans:
        logger.warning(
            f"No resection-plan found for patient {record.patient_id} — cannot hydrate lesion list"
        )
        return []

    plan = plans[0]
    lesions = (plan.data or {}).get("lesions", [])

    result: list[dict[str, Any]] = []
    for item in lesions:
        num = item.get("lesion_num")
        if num is not None:
            result.append({"const": num, "title": f"Очаг #{num}"})

    return result
