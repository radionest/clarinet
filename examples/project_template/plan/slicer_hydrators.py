"""Custom slicer context hydrators for the project.

Loaded automatically at startup. The path is wired in settings.toml via
``config_context_hydrators_file``.

Each hydrator returns a dict whose keys become variables in Slicer scripts and
validators. Return ``{}`` if the data is unavailable — never raise.

See `.claude/rules/slicer.md` (Part A) for the full reference.
"""

from typing import Any

from clarinet.models.record import RecordRead
from clarinet.repositories.record_repository import RecordSearchCriteria
from clarinet.services.slicer.context_hydration import (
    SlicerHydrationContext,
    slicer_context_hydrator,
)


@slicer_context_hydrator("best_series_from_first_check")
async def hydrate_best_series_from_first_check(
    record: RecordRead,
    _context: dict[str, Any],
    ctx: SlicerHydrationContext,
) -> dict[str, Any]:
    """Inject ``best_series_uid`` from the first-check record of the same study.

    The Slicer script (and validator) will see ``best_series_uid`` as a global
    variable. If no first-check exists or it doesn't have ``best_series`` in its
    data, returns ``{}`` and the script must handle ``None``.
    """
    criteria = RecordSearchCriteria(
        record_type_name="first-check",
        study_uid=record.study_uid,
    )
    first_checks = await ctx.record_repo.find_by_criteria(criteria)
    if not first_checks:
        return {}

    best_series = (first_checks[0].data or {}).get("best_series")
    if not best_series:
        return {}

    return {"best_series_uid": best_series}


# TODO: add project-specific hydrators here.
