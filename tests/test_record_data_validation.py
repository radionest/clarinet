"""Tests for record data validation — registry, decorator, runner, JSON Schema integration.

Covers:
- @record_validator decorator registration + duplicate-name guard
- run_record_validators aggregates errors across multiple validators
- run_on_partial flag controls behavior in partial mode
- Unknown validator name at runtime is logged and skipped (fail-fast lives in reconcile)
- load_custom_validators imports from file via importlib
- ValidatorContext.from_session wires all four repositories
- validate_json_by_schema converts jsonschema errors to RecordDataValidationError
  with JSON Pointer paths and validator-name codes; caps at _MAX_SCHEMA_ERRORS
"""

import textwrap
from unittest.mock import AsyncMock, MagicMock

import pytest

from clarinet.exceptions.domain import (
    FieldError,
    RecordDataValidationError,
)
from clarinet.services.record_data_validation import (
    _VALIDATOR_REGISTRY,
    ValidatorContext,
    load_custom_validators,
    record_validator,
    run_record_validators,
)
from clarinet.utils.validation import (
    _MAX_SCHEMA_ERRORS,
    validate_json_by_schema,
    validate_json_by_schema_partial,
)


@pytest.fixture(autouse=True)
def _clean_registry(isolated_validator_registry):
    """Apply ``isolated_validator_registry`` to every test in this file."""


def _make_record(validator_names: list[str] | None = None) -> MagicMock:
    """Build a minimal Record mock with record_type.data_validators set."""
    record = MagicMock()
    record.record_type = MagicMock()
    record.record_type.data_validators = validator_names
    return record


# ---------------------------------------------------------------------------
# Decorator registration
# ---------------------------------------------------------------------------


class TestRecordValidatorDecorator:
    def test_registers_validator_with_run_on_partial(self):
        @record_validator("test.alpha", run_on_partial=True)
        async def alpha(record, data, ctx):
            return None

        spec = _VALIDATOR_REGISTRY["test.alpha"]
        assert spec.func is alpha
        assert spec.run_on_partial is True

    def test_run_on_partial_defaults_to_false(self):
        @record_validator("test.beta")
        async def beta(record, data, ctx):
            return None

        assert _VALIDATOR_REGISTRY["test.beta"].run_on_partial is False

    def test_duplicate_name_raises_value_error(self):
        @record_validator("test.dup")
        async def first(record, data, ctx):
            return None

        with pytest.raises(ValueError, match="already registered"):

            @record_validator("test.dup")
            async def second(record, data, ctx):
                return None


# ---------------------------------------------------------------------------
# run_record_validators — aggregation, partial filtering, unknown-name skip
# ---------------------------------------------------------------------------


class TestRunRecordValidators:
    @pytest.mark.asyncio
    async def test_no_validators_returns_silently(self):
        """Empty/None data_validators list → no-op (no errors)."""
        await run_record_validators(_make_record(None), {}, AsyncMock(), partial=False)
        await run_record_validators(_make_record([]), {}, AsyncMock(), partial=False)

    @pytest.mark.asyncio
    async def test_aggregates_errors_from_multiple_validators(self):
        """Errors from every validator are merged into one RecordDataValidationError."""

        @record_validator("test.first_fail")
        async def first(record, data, ctx):
            raise RecordDataValidationError([FieldError(path="/a", message="bad A", code="bad_a")])

        @record_validator("test.second_fail")
        async def second(record, data, ctx):
            raise RecordDataValidationError([FieldError(path="/b", message="bad B", code="bad_b")])

        record = _make_record(["test.first_fail", "test.second_fail"])
        with pytest.raises(RecordDataValidationError) as exc_info:
            await run_record_validators(record, {}, AsyncMock(), partial=False)

        paths = sorted(e.path for e in exc_info.value.errors)
        assert paths == ["/a", "/b"]

    @pytest.mark.asyncio
    async def test_passes_when_all_validators_succeed(self):
        called: list[str] = []

        @record_validator("test.ok_a")
        async def a(record, data, ctx):
            called.append("a")

        @record_validator("test.ok_b")
        async def b(record, data, ctx):
            called.append("b")

        record = _make_record(["test.ok_a", "test.ok_b"])
        await run_record_validators(record, {}, AsyncMock(), partial=False)
        assert called == ["a", "b"]

    @pytest.mark.asyncio
    async def test_partial_skips_non_partial_validator(self):
        """When partial=True, run_on_partial=False validators must NOT execute."""
        called: list[str] = []

        @record_validator("test.skip_in_partial", run_on_partial=False)
        async def skip_me(record, data, ctx):
            called.append("skip_me")
            raise RecordDataValidationError([FieldError(path="/x", message="bad", code="x")])

        record = _make_record(["test.skip_in_partial"])
        # Should not raise — validator skipped in partial mode
        await run_record_validators(record, {}, AsyncMock(), partial=True)
        assert called == []

    @pytest.mark.asyncio
    async def test_partial_runs_run_on_partial_validators(self):
        called: list[str] = []

        @record_validator("test.run_in_partial", run_on_partial=True)
        async def run_me(record, data, ctx):
            called.append("run_me")

        record = _make_record(["test.run_in_partial"])
        await run_record_validators(record, {}, AsyncMock(), partial=True)
        assert called == ["run_me"]

    @pytest.mark.asyncio
    async def test_unknown_name_logged_and_skipped(self, monkeypatch):
        """Runtime miss (registry change after reconcile) is a logged skip, not a crash.

        Loguru bypasses Python's logging module and writes to its own stderr sink,
        which pytest's caplog/capsys do not capture — patch ``logger.error`` directly.
        """
        import clarinet.services.record_data_validation as mod

        captured: list[str] = []
        monkeypatch.setattr(mod.logger, "error", lambda msg, *a, **kw: captured.append(str(msg)))

        # No validator registered under "test.ghost"
        record = _make_record(["test.ghost"])
        # Should not raise — reconcile fail-fast is the primary guard;
        # at runtime we keep the request alive but log loudly.
        await run_record_validators(record, {}, AsyncMock(), partial=False)
        assert any("test.ghost" in m for m in captured)


# ---------------------------------------------------------------------------
# load_custom_validators (importlib loader)
# ---------------------------------------------------------------------------


class TestLoadCustomValidators:
    def test_missing_folder_returns_zero(self, tmp_path):
        count = load_custom_validators(tmp_path / "nonexistent")
        assert count == 0

    def test_folder_without_validators_file_returns_zero(self, tmp_path):
        count = load_custom_validators(tmp_path)
        assert count == 0

    def test_loads_valid_validator_file(self, tmp_path):
        validators_file = tmp_path / "validators.py"
        validators_file.write_text(
            textwrap.dedent("""\
            from clarinet.services.record_data_validation import record_validator

            @record_validator("loaded.test_validator")
            async def loaded_test(record, data, ctx):
                return None
            """)
        )

        count = load_custom_validators(tmp_path)
        assert count == 1
        assert "loaded.test_validator" in _VALIDATOR_REGISTRY

    def test_broken_file_returns_zero_does_not_crash(self, tmp_path):
        (tmp_path / "validators.py").write_text("raise RuntimeError('import error')")
        # Must not propagate the exception
        count = load_custom_validators(tmp_path)
        assert count == 0


# ---------------------------------------------------------------------------
# ValidatorContext
# ---------------------------------------------------------------------------


class TestValidatorContext:
    def test_from_session_builds_all_four_repos(self):
        ctx = ValidatorContext.from_session(AsyncMock())
        assert ctx.record_repo is not None
        assert ctx.study_repo is not None
        assert ctx.user_repo is not None
        assert ctx.record_type_repo is not None


# ---------------------------------------------------------------------------
# JSON Schema → RecordDataValidationError
# ---------------------------------------------------------------------------


class TestJsonSchemaErrors:
    def test_validation_failure_becomes_record_data_validation_error(self):
        schema = {
            "type": "object",
            "properties": {"score": {"type": "integer", "minimum": 0}},
            "required": ["score"],
        }
        # Missing required field
        with pytest.raises(RecordDataValidationError) as exc_info:
            validate_json_by_schema({}, schema)
        err = exc_info.value.errors[0]
        # Empty path means "document root" — jsonschema reports missing-required at root
        assert err.path == ""
        assert err.code == "required"

    def test_path_is_json_pointer(self):
        schema = {
            "type": "object",
            "properties": {
                "mappings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"new_id": {"type": "integer", "minimum": 1}},
                        "required": ["new_id"],
                    },
                }
            },
        }
        # mappings[1].new_id violates minimum
        data = {"mappings": [{"new_id": 5}, {"new_id": 0}]}
        with pytest.raises(RecordDataValidationError) as exc_info:
            validate_json_by_schema(data, schema)
        paths = [e.path for e in exc_info.value.errors]
        assert "/mappings/1/new_id" in paths

    def test_caps_errors_at_max(self):
        """Many validation issues are bounded by _MAX_SCHEMA_ERRORS."""
        # Schema requires every item to be a non-empty string; feed many empty strings
        schema = {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
        }
        data = [""] * (_MAX_SCHEMA_ERRORS + 5)
        with pytest.raises(RecordDataValidationError) as exc_info:
            validate_json_by_schema(data, schema)
        assert len(exc_info.value.errors) == _MAX_SCHEMA_ERRORS

    def test_valid_data_does_not_raise(self):
        validate_json_by_schema({"score": 5}, {"type": "object"})

    def test_partial_strips_required(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        # `required` stripped — empty dict is OK.
        validate_json_by_schema_partial({}, schema)

    def test_partial_still_checks_types(self):
        schema = {
            "type": "object",
            "properties": {"age": {"type": "integer"}},
            "required": ["age"],
        }
        # Missing OK (required stripped) but wrong type still fails.
        with pytest.raises(RecordDataValidationError) as exc_info:
            validate_json_by_schema_partial({"age": "twelve"}, schema)
        assert exc_info.value.errors[0].code == "type"
