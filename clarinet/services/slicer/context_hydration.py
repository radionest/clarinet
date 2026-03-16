"""Slicer context hydration — decorator-based registry for async context enrichment.

Hydrators are async functions that receive a ``RecordRead``, the current context dict,
and a ``SlicerHydrationContext`` (repository access).  They return a dict of extra
key-value pairs to merge into the Slicer script context.

Built-in hydrators are registered at import time; project-specific ones can be loaded
from a ``context_hydrators.py`` file in the tasks folder.

Pattern mirrors ``clarinet/services/schema_hydration.py``.
"""

import importlib.util
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

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

_SLICER_HYDRATOR_REGISTRY: dict[str, SlicerHydratorFunc] = {}


def slicer_context_hydrator(source_name: str) -> Callable[[SlicerHydratorFunc], SlicerHydratorFunc]:
    """Register a slicer context hydrator by name.

    Args:
        source_name: Unique name referenced in ``RecordType.slicer_context_hydrators``.

    Returns:
        Decorator that registers the function and returns it unchanged.
    """

    def decorator(func: SlicerHydratorFunc) -> SlicerHydratorFunc:
        _SLICER_HYDRATOR_REGISTRY[source_name] = func
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
            logger.warning(f"Unknown slicer context hydrator '{name}' — skipping")
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
    """Load ``context_hydrators.py`` from *folder* via importlib.

    Decorators in the loaded file auto-register into ``_SLICER_HYDRATOR_REGISTRY``.

    Args:
        folder: Directory that may contain a ``context_hydrators.py`` file.

    Returns:
        Number of *new* hydrators added (0 if file not found).
    """
    from clarinet.settings import settings

    path = Path(folder) / settings.config_context_hydrators_file
    if not path.exists():
        return 0

    before = set(_SLICER_HYDRATOR_REGISTRY)

    # If hydrators file is in a subdirectory, add its parent to sys.path
    folder_str = str(Path(folder).resolve())
    parent_str = str(path.parent.resolve())
    added_parent = parent_str != folder_str and parent_str not in sys.path
    if added_parent:
        sys.path.insert(0, parent_str)

    try:
        module_name = "clarinet_custom_slicer_hydrators"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.error(f"Cannot create module spec for {path}")
            return 0
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception:
        logger.exception(f"Error loading custom slicer context hydrators from {path}")
        return 0
    finally:
        if added_parent and parent_str in sys.path:
            sys.path.remove(parent_str)

    added = set(_SLICER_HYDRATOR_REGISTRY) - before
    if added:
        logger.info(
            f"Loaded {len(added)} custom slicer context hydrator(s): {', '.join(sorted(added))}"
        )
    return len(added)
