"""Unit tests for schema hydration service."""

import copy
from unittest.mock import AsyncMock, MagicMock

import pytest

from clarinet.services.schema_hydration import (
    _HYDRATOR_REGISTRY,
    HydrationContext,
    hydrate_schema,
    hydrate_study_series,
    schema_hydrator,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Save and restore hydrator registry between tests."""
    original = dict(_HYDRATOR_REGISTRY)
    yield
    _HYDRATOR_REGISTRY.clear()
    _HYDRATOR_REGISTRY.update(original)


def _make_record(study_uid: str | None = "1.2.3") -> MagicMock:
    """Create a mock Record with configurable study_uid."""
    record = MagicMock()
    record.study_uid = study_uid
    return record


def _make_session() -> AsyncMock:
    """Create a mock async session."""
    return AsyncMock()


def _make_context() -> MagicMock:
    """Create a mock HydrationContext."""
    ctx = MagicMock(spec=HydrationContext)
    ctx.study_repo = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# Decorator registration
# ---------------------------------------------------------------------------


class TestSchemaHydrator:
    def test_registers_function(self):
        @schema_hydrator("test_source")
        async def my_hydrator(record, options, ctx):
            return []

        assert "test_source" in _HYDRATOR_REGISTRY
        assert _HYDRATOR_REGISTRY["test_source"] is my_hydrator

    def test_overwrites_existing(self):
        @schema_hydrator("overwrite_me")
        async def first(record, options, ctx):
            return []

        @schema_hydrator("overwrite_me")
        async def second(record, options, ctx):
            return []

        assert _HYDRATOR_REGISTRY["overwrite_me"] is second


# ---------------------------------------------------------------------------
# Built-in hydrator: study_series
# ---------------------------------------------------------------------------


class TestHydrateStudySeries:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_study_uid(self):
        record = _make_record(study_uid=None)
        result = await hydrate_study_series(record, {}, _make_context())
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_series_options(self):
        series1 = MagicMock()
        series1.series_uid = "1.2.3.1"
        series1.series_number = 1
        series1.series_description = "Axial T2"
        series1.modality = "MR"
        series1.instance_count = 42

        series2 = MagicMock()
        series2.series_uid = "1.2.3.2"
        series2.series_number = 2
        series2.series_description = None
        series2.modality = "CT"
        series2.instance_count = None

        study = MagicMock()
        study.series = [series1, series2]

        ctx = _make_context()
        ctx.study_repo.get_with_series = AsyncMock(return_value=study)

        record = _make_record(study_uid="1.2.3")
        result = await hydrate_study_series(record, {}, ctx)

        assert len(result) == 2
        assert result[0]["const"] == "1.2.3.1"
        assert "#1" in result[0]["title"]
        assert "Axial T2" in result[0]["title"]
        assert "MR" in result[0]["title"]
        assert "42 img" in result[0]["title"]

        assert result[1]["const"] == "1.2.3.2"
        assert "#2" in result[1]["title"]
        assert "CT" in result[1]["title"]

    @pytest.mark.asyncio
    async def test_label_without_optional_fields(self):
        """Series with no description, no modality, no instance_count."""
        series = MagicMock()
        series.series_uid = "1.2.3.99"
        series.series_number = 5
        series.series_description = None
        series.modality = None
        series.instance_count = None

        study = MagicMock()
        study.series = [series]

        ctx = _make_context()
        ctx.study_repo.get_with_series = AsyncMock(return_value=study)

        record = _make_record(study_uid="1.2.3")
        result = await hydrate_study_series(record, {}, ctx)

        assert len(result) == 1
        assert result[0]["title"] == "#5"

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self):
        ctx = _make_context()
        ctx.study_repo.get_with_series = AsyncMock(side_effect=Exception("DB error"))

        record = _make_record(study_uid="1.2.3")
        result = await hydrate_study_series(record, {}, ctx)

        assert result == []


# ---------------------------------------------------------------------------
# Schema walker: hydrate_schema
# ---------------------------------------------------------------------------


class TestHydrateSchema:
    @pytest.mark.asyncio
    async def test_deep_copy_not_mutated(self):
        original = {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "x-options": {"source": "test_src"},
                }
            },
        }
        original_copy = copy.deepcopy(original)

        @schema_hydrator("test_src")
        async def _hydrator(record, options, ctx):
            return [{"const": "a", "title": "A"}]

        await hydrate_schema(original, _make_record(), _make_session())
        assert original == original_copy

    @pytest.mark.asyncio
    async def test_resolves_x_options_in_properties(self):
        schema = {
            "type": "object",
            "properties": {
                "series_field": {
                    "type": "string",
                    "pattern": "^[0-9.]+$",
                    "x-options": {"source": "test_prop"},
                },
                "normal_field": {"type": "string"},
            },
        }

        @schema_hydrator("test_prop")
        async def _hydrator(record, options, ctx):
            return [{"const": "uid1", "title": "Series 1"}]

        result = await hydrate_schema(schema, _make_record(), _make_session())

        field = result["properties"]["series_field"]
        assert field["oneOf"] == [{"const": "uid1", "title": "Series 1"}]
        assert "x-options" not in field
        assert "pattern" not in field
        # Normal field unchanged
        assert result["properties"]["normal_field"] == {"type": "string"}

    @pytest.mark.asyncio
    async def test_resolves_x_options_in_then(self):
        schema = {
            "type": "object",
            "if": {"properties": {"cond": {"const": True}}},
            "then": {
                "properties": {
                    "dynamic": {
                        "type": "string",
                        "x-options": {"source": "then_src"},
                    }
                }
            },
        }

        @schema_hydrator("then_src")
        async def _hydrator(record, options, ctx):
            return [{"const": "v", "title": "V"}]

        result = await hydrate_schema(schema, _make_record(), _make_session())
        assert result["then"]["properties"]["dynamic"]["oneOf"] == [{"const": "v", "title": "V"}]

    @pytest.mark.asyncio
    async def test_resolves_x_options_in_allof(self):
        schema = {
            "allOf": [
                {
                    "properties": {
                        "nested": {
                            "type": "string",
                            "x-options": {"source": "allof_src"},
                        }
                    }
                }
            ]
        }

        @schema_hydrator("allof_src")
        async def _hydrator(record, options, ctx):
            return [{"const": "x", "title": "X"}]

        result = await hydrate_schema(schema, _make_record(), _make_session())
        assert result["allOf"][0]["properties"]["nested"]["oneOf"] == [{"const": "x", "title": "X"}]

    @pytest.mark.asyncio
    async def test_unknown_source_leaves_field_unchanged(self):
        schema = {
            "properties": {
                "field": {
                    "type": "string",
                    "x-options": {"source": "nonexistent"},
                    "pattern": "^[0-9]+$",
                }
            }
        }

        result = await hydrate_schema(schema, _make_record(), _make_session())
        field = result["properties"]["field"]
        assert "oneOf" not in field
        assert field["x-options"] == {"source": "nonexistent"}
        assert field["pattern"] == "^[0-9]+$"

    @pytest.mark.asyncio
    async def test_empty_result_leaves_field_unchanged(self):
        schema = {
            "properties": {
                "field": {
                    "type": "string",
                    "x-options": {"source": "empty_src"},
                    "pattern": "^.*$",
                }
            }
        }

        @schema_hydrator("empty_src")
        async def _hydrator(record, options, ctx):
            return []

        result = await hydrate_schema(schema, _make_record(), _make_session())
        field = result["properties"]["field"]
        assert "oneOf" not in field
        assert "x-options" in field

    @pytest.mark.asyncio
    async def test_hydrator_exception_leaves_field_unchanged(self):
        schema = {
            "properties": {
                "field": {
                    "type": "string",
                    "x-options": {"source": "error_src"},
                }
            }
        }

        @schema_hydrator("error_src")
        async def _hydrator(record, options, ctx):
            raise RuntimeError("boom")

        result = await hydrate_schema(schema, _make_record(), _make_session())
        field = result["properties"]["field"]
        assert "oneOf" not in field

    @pytest.mark.asyncio
    async def test_no_x_options_returns_copy(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }

        result = await hydrate_schema(schema, _make_record(), _make_session())
        assert result == schema
        assert result is not schema

    @pytest.mark.asyncio
    async def test_passes_full_x_options_to_hydrator(self):
        """Hydrator receives the full x-options dict, not just source."""
        received_options = {}

        @schema_hydrator("params_src")
        async def _hydrator(record, options, ctx):
            received_options.update(options)
            return [{"const": "v", "title": "V"}]

        schema = {
            "properties": {
                "field": {
                    "type": "string",
                    "x-options": {
                        "source": "params_src",
                        "modality_filter": "CT",
                    },
                }
            }
        }

        await hydrate_schema(schema, _make_record(), _make_session())
        assert received_options["source"] == "params_src"
        assert received_options["modality_filter"] == "CT"
