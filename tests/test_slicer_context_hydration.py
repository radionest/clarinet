"""Tests for slicer context hydration — registry, decorator, loader, error handling.

Covers:
- Decorator registers hydrators
- hydrate_slicer_context runs matching hydrators and merges results
- Unknown hydrator names are skipped with warning
- Hydrator exceptions are caught and skipped
- Empty hydrator_names is a no-op
- load_custom_slicer_hydrators loads from file
"""

import textwrap
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from clarinet.services.slicer.context_hydration import (
    _SLICER_HYDRATOR_REGISTRY,
    SlicerHydrationContext,
    hydrate_slicer_context,
    load_custom_slicer_hydrators,
    slicer_context_hydrator,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save and restore the hydrator registry around each test."""
    saved = dict(_SLICER_HYDRATOR_REGISTRY)
    yield
    _SLICER_HYDRATOR_REGISTRY.clear()
    _SLICER_HYDRATOR_REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Decorator registration
# ---------------------------------------------------------------------------


def test_decorator_registers_hydrator():
    """@slicer_context_hydrator registers a function by name."""

    @slicer_context_hydrator("test_hydrator")
    async def my_hydrator(record: Any, context: Any, ctx: Any) -> dict[str, Any]:
        return {"key": "value"}

    assert "test_hydrator" in _SLICER_HYDRATOR_REGISTRY
    assert _SLICER_HYDRATOR_REGISTRY["test_hydrator"] is my_hydrator


def test_decorator_overwrites_existing():
    """Registering the same name twice replaces the first."""

    @slicer_context_hydrator("dup")
    async def first(record: Any, context: Any, ctx: Any) -> dict[str, Any]:
        return {"v": 1}

    @slicer_context_hydrator("dup")
    async def second(record: Any, context: Any, ctx: Any) -> dict[str, Any]:
        return {"v": 2}

    assert _SLICER_HYDRATOR_REGISTRY["dup"] is second


# ---------------------------------------------------------------------------
# hydrate_slicer_context
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hydrate_merges_results():
    """Hydrator results are merged into context."""

    @slicer_context_hydrator("add_uid")
    async def add_uid(record: Any, context: Any, ctx: Any) -> dict[str, Any]:
        return {"best_study_uid": "1.2.3.4"}

    mock_session = AsyncMock()
    context: dict[str, Any] = {"working_folder": "/tmp"}
    record = MagicMock()

    result = await hydrate_slicer_context(context, record, mock_session, ["add_uid"])

    assert result["best_study_uid"] == "1.2.3.4"
    assert result["working_folder"] == "/tmp"


@pytest.mark.asyncio
async def test_hydrate_empty_names_is_noop():
    """Empty or None hydrator_names returns context unchanged."""
    context: dict[str, Any] = {"key": "value"}
    mock_session = AsyncMock()
    record = MagicMock()

    result_none = await hydrate_slicer_context(context.copy(), record, mock_session, None)
    assert result_none == {"key": "value"}

    result_empty = await hydrate_slicer_context(context.copy(), record, mock_session, [])
    assert result_empty == {"key": "value"}


@pytest.mark.asyncio
async def test_hydrate_unknown_name_skipped():
    """Unknown hydrator name is skipped (not raised)."""
    context: dict[str, Any] = {"key": "value"}
    mock_session = AsyncMock()
    record = MagicMock()

    result = await hydrate_slicer_context(context, record, mock_session, ["nonexistent"])
    assert result == {"key": "value"}


@pytest.mark.asyncio
async def test_hydrate_exception_skipped():
    """Hydrator that raises is skipped, others still run."""

    @slicer_context_hydrator("broken")
    async def broken(record: Any, context: Any, ctx: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    @slicer_context_hydrator("healthy")
    async def healthy(record: Any, context: Any, ctx: Any) -> dict[str, Any]:
        return {"healthy": True}

    context: dict[str, Any] = {}
    mock_session = AsyncMock()
    record = MagicMock()

    result = await hydrate_slicer_context(context, record, mock_session, ["broken", "healthy"])
    assert result.get("healthy") is True


@pytest.mark.asyncio
async def test_hydrate_empty_return_no_update():
    """Hydrator returning empty dict does not pollute context."""

    @slicer_context_hydrator("empty_return")
    async def empty_return(record: Any, context: Any, ctx: Any) -> dict[str, Any]:
        return {}

    context: dict[str, Any] = {"existing": 1}
    mock_session = AsyncMock()
    record = MagicMock()

    result = await hydrate_slicer_context(context, record, mock_session, ["empty_return"])
    assert result == {"existing": 1}


# ---------------------------------------------------------------------------
# load_custom_slicer_hydrators
# ---------------------------------------------------------------------------


def test_load_from_missing_folder(tmp_path):
    """Missing folder returns 0."""
    count = load_custom_slicer_hydrators(tmp_path / "nonexistent")
    assert count == 0


def test_load_from_folder_without_file(tmp_path):
    """Folder without context_hydrators.py returns 0."""
    count = load_custom_slicer_hydrators(tmp_path)
    assert count == 0


def test_load_valid_hydrator(tmp_path):
    """Valid context_hydrators.py registers hydrators."""
    hydrator_file = tmp_path / "context_hydrators.py"
    hydrator_file.write_text(
        textwrap.dedent("""\
        from clarinet.services.slicer.context_hydration import slicer_context_hydrator

        @slicer_context_hydrator("loaded_test")
        async def loaded_test(record, context, ctx):
            return {"loaded": True}
        """)
    )

    count = load_custom_slicer_hydrators(tmp_path)
    assert count == 1
    assert "loaded_test" in _SLICER_HYDRATOR_REGISTRY


def test_load_broken_file(tmp_path):
    """Broken file returns 0, does not crash."""
    hydrator_file = tmp_path / "context_hydrators.py"
    hydrator_file.write_text("raise RuntimeError('import error')")

    count = load_custom_slicer_hydrators(tmp_path)
    assert count == 0


# ---------------------------------------------------------------------------
# SlicerHydrationContext
# ---------------------------------------------------------------------------


def test_hydration_context_from_session():
    """from_session creates context with StudyRepository."""
    mock_session = AsyncMock()
    ctx = SlicerHydrationContext.from_session(mock_session)
    assert ctx.study_repo is not None
