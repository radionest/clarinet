"""Slicer context hydration — decorator-based registry for async context enrichment.

Hydrators are async functions that receive a ``RecordRead``, the current context dict,
and a ``SlicerHydrationContext`` (repository access).  They return a dict of extra
key-value pairs to merge into the Slicer script context.

Built-in hydrators are registered at import time; project-specific ones can be loaded
from a ``context_hydrators.py`` file in the tasks folder.

Pattern mirrors ``clarinet/services/schema_hydration.py``.
"""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.config.custom_registry import CustomCodeRegistry
from clarinet.repositories.record_repository import RecordRepository
from clarinet.repositories.study_repository import StudyRepository
from clarinet.utils.logger import logger

if True:  # avoid TYPE_CHECKING for runtime import
    from clarinet.models.record import RecordRead


@dataclass(frozen=True, slots=True)
class SlicerHydrationContext:
    """Pre-built dependencies available to slicer context hydrators."""

    study_repo: StudyRepository
    record_repo: RecordRepository

    @classmethod
    def from_session(cls, session: AsyncSession) -> "SlicerHydrationContext":
        """Build context from a DB session.

        Args:
            session: Async DB session for the current request.
        """
        return cls(
            study_repo=StudyRepository(session),
            record_repo=RecordRepository(session),
        )


type SlicerHydratorFunc = Callable[
    [RecordRead, dict[str, Any], SlicerHydrationContext],
    Coroutine[Any, Any, dict[str, Any]],
]

_SLICER_HYDRATOR_REGISTRY: CustomCodeRegistry[SlicerHydratorFunc] = CustomCodeRegistry(
    filename_setting="config_context_hydrators_file",
    module_name="clarinet_custom_slicer_hydrators",
    label="slicer context hydrator",
)


def get_registered_slicer_hydrator_names() -> frozenset[str]:
    """Return the set of currently registered slicer hydrator names.

    Public accessor for the module-private ``_SLICER_HYDRATOR_REGISTRY`` —
    intended for reconcile-time validation in :func:`bootstrap.reconcile_config`
    (mirrors ``record_data_validation.get_registered_validator_names``).
    """
    return _SLICER_HYDRATOR_REGISTRY.names()


def slicer_context_hydrator(source_name: str) -> Callable[[SlicerHydratorFunc], SlicerHydratorFunc]:
    """Register a slicer context hydrator by name.

    Args:
        source_name: Unique name referenced in ``RecordType.slicer_context_hydrators``.

    Returns:
        Decorator that registers the function and returns it unchanged.
    """

    def decorator(func: SlicerHydratorFunc) -> SlicerHydratorFunc:
        _SLICER_HYDRATOR_REGISTRY.register(source_name, func)
        return func

    return decorator


async def hydrate_slicer_context(
    context: dict[str, Any],
    record: RecordRead,
    session: AsyncSession,
    hydrator_names: list[str] | None,
) -> dict[str, Any]:
    """Run registered hydrators and merge results into context.

    Args:
        context: Base context dict (mutated in place and returned).
        record: Fully-loaded ``RecordRead`` with relations.
        session: Async DB session.
        hydrator_names: List of hydrator names to run. ``None`` or empty = no-op.

    Returns:
        The enriched context dict.
    """
    if not hydrator_names:
        return context

    ctx = SlicerHydrationContext.from_session(session)

    for name in hydrator_names:
        hydrator = _SLICER_HYDRATOR_REGISTRY.get(name)
        if hydrator is None:
            # A missing hydrator breaks the doctor's Slicer-open flow.
            # Reconcile fail-fasts on config-defined RecordTypes at startup,
            # but types mutated via the API (TOML mode) and orphaned DB rows
            # bypass that guard — this runtime error is their only signal.
            logger.error(f"Unknown slicer context hydrator '{name}' — skipping")
            continue
        try:
            extra = await hydrator(record, context, ctx)
        except Exception:
            logger.exception(f"Slicer context hydrator '{name}' raised an exception — skipping")
            continue
        if extra:
            context.update(extra)

    return context


def load_custom_slicer_hydrators(folder: str | Path) -> int:
    """Load ``context_hydrators.py`` (``settings.config_context_hydrators_file``)
    from *folder*.

    Decorators in the loaded file auto-register into ``_SLICER_HYDRATOR_REGISTRY``.

    Args:
        folder: Config root that may contain the hydrators file.

    Returns:
        Number of *new* hydrators added (0 if file not found).

    Raises:
        ConfigLoadError: If the file exists but fails to import.
    """
    return _SLICER_HYDRATOR_REGISTRY.load_from(folder)
