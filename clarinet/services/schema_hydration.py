"""
Schema hydration service for resolving dynamic field options.

Walks a JSON Schema, finds fields with ``x-options`` markers, and resolves them
to ``oneOf`` arrays via registered hydrator callbacks.  Built-in hydrators
(e.g. ``study_series``) are registered at import time; project-specific ones
can be loaded from a ``hydrators.py`` file in the tasks folder.
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.models.record import Record
from clarinet.repositories.study_repository import StudyRepository
from clarinet.utils.logger import logger


@dataclass(frozen=True, slots=True)
class HydrationContext:
    """Pre-built dependencies available to hydrator callbacks.

    Hydrators receive this context instead of a raw ``AsyncSession``,
    ensuring they only access data through repository interfaces.
    """

    study_repo: StudyRepository

    @classmethod
    def from_session(cls, session: AsyncSession) -> HydrationContext:
        """Build context from a DB session.

        Args:
            session: Async DB session for the current request.
        """
        return cls(study_repo=StudyRepository(session))


type HydratorFunc = Callable[
    [Record, dict[str, Any], HydrationContext],
    Coroutine[Any, Any, list[dict[str, Any]]],
]

_HYDRATOR_REGISTRY: dict[str, HydratorFunc] = {}


def schema_hydrator(source_name: str) -> Callable[[HydratorFunc], HydratorFunc]:
    """Register a hydrator for a given ``x-options`` source name.

    Args:
        source_name: Value of ``x-options.source`` that triggers this hydrator.

    Returns:
        Decorator that registers the function and returns it unchanged.
    """

    def decorator(func: HydratorFunc) -> HydratorFunc:
        _HYDRATOR_REGISTRY[source_name] = func
        return func

    return decorator


# ---------------------------------------------------------------------------
# Built-in hydrator: study_series
# ---------------------------------------------------------------------------


@schema_hydrator("study_series")
async def hydrate_study_series(
    record: Record,
    options: dict[str, Any],
    ctx: HydrationContext,
) -> list[dict[str, Any]]:
    """Return series belonging to the record's study as ``oneOf`` items.

    Label format: ``#<number> <description> (<modality>, <count> img)``
    """
    if not record.study_uid:
        return []

    try:
        study = await ctx.study_repo.get_with_series(record.study_uid)
    except Exception:
        logger.warning(f"Failed to load study {record.study_uid} for hydration")
        return []

    result: list[dict[str, Any]] = []
    for s in study.series:
        parts: list[str] = [f"#{s.series_number}"]
        if s.series_description:
            parts.append(s.series_description)
        meta: list[str] = []
        if s.modality:
            meta.append(s.modality)
        if s.instance_count is not None:
            meta.append(f"{s.instance_count} img")
        if meta:
            parts.append(f"({', '.join(meta)})")
        title = " ".join(parts)
        result.append({"const": s.series_uid, "title": title})

    return result


# ---------------------------------------------------------------------------
# Schema walker
# ---------------------------------------------------------------------------

_CONTAINER_KEYS = ("allOf", "anyOf", "oneOf")
_BRANCH_KEYS = ("if", "then", "else")


async def hydrate_schema(
    schema: dict[str, Any],
    record: Record,
    session: AsyncSession,
) -> dict[str, Any]:
    """Deep-copy *schema* and resolve every ``x-options`` marker to ``oneOf``.

    Args:
        schema: Original JSON Schema (not mutated).
        record: Record whose context drives hydration.
        session: Async DB session for hydrator queries.

    Returns:
        A new schema dict with ``x-options`` fields resolved where possible.
    """
    ctx = HydrationContext.from_session(session)
    result = copy.deepcopy(schema)
    await _walk(result, record, ctx)
    return result


async def _walk(node: dict[str, Any], record: Record, ctx: HydrationContext) -> None:
    """Recursively walk a schema node, hydrating ``x-options`` in-place."""

    # Process properties
    properties: dict[str, Any] | None = node.get("properties")
    if isinstance(properties, dict):
        for field_schema in properties.values():
            if isinstance(field_schema, dict):
                await _hydrate_field(field_schema, record, ctx)
                await _walk(field_schema, record, ctx)

    # Process branch keywords (if/then/else)
    for key in _BRANCH_KEYS:
        branch = node.get(key)
        if isinstance(branch, dict):
            await _walk(branch, record, ctx)

    # Process container keywords (allOf/anyOf/oneOf)
    for key in _CONTAINER_KEYS:
        container = node.get(key)
        if isinstance(container, list):
            for item in container:
                if isinstance(item, dict):
                    await _walk(item, record, ctx)

    # Process items (for array schemas)
    items = node.get("items")
    if isinstance(items, dict):
        await _walk(items, record, ctx)


async def _hydrate_field(
    field_schema: dict[str, Any],
    record: Record,
    ctx: HydrationContext,
) -> None:
    """Resolve a single field's ``x-options`` marker, if present."""
    x_options = field_schema.get("x-options")
    if not isinstance(x_options, dict):
        return

    source = x_options.get("source")
    if not source:
        logger.warning("x-options without 'source' key — skipping")
        return

    hydrator = _HYDRATOR_REGISTRY.get(source)
    if hydrator is None:
        logger.warning(f"Unknown x-options source '{source}' — skipping")
        return

    try:
        options_list = await hydrator(record, x_options, ctx)
    except Exception:
        logger.exception(f"Hydrator '{source}' raised an exception — skipping")
        return

    if not options_list:
        return

    # Replace field: set oneOf, remove x-options and pattern
    field_schema["oneOf"] = options_list
    field_schema.pop("x-options", None)
    field_schema.pop("pattern", None)


# ---------------------------------------------------------------------------
# Custom hydrator loader
# ---------------------------------------------------------------------------


def load_custom_hydrators(folder: str | Path) -> int:
    """Load ``hydrators.py`` from *folder* via importlib.

    Decorators in the loaded file auto-register into ``_HYDRATOR_REGISTRY``.
    Built-in hydrators are never cleared.

    Args:
        folder: Directory that may contain a ``hydrators.py`` file.

    Returns:
        Number of *new* hydrators added (0 if file not found).
    """
    path = Path(folder) / "hydrators.py"
    if not path.exists():
        return 0

    before = set(_HYDRATOR_REGISTRY)

    try:
        module_name = "clarinet_custom_hydrators"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.error(f"Cannot create module spec for {path}")
            return 0
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    except Exception:
        logger.exception(f"Error loading custom hydrators from {path}")
        return 0

    added = set(_HYDRATOR_REGISTRY) - before
    if added:
        logger.info(f"Loaded {len(added)} custom hydrator(s): {', '.join(sorted(added))}")
    return len(added)
