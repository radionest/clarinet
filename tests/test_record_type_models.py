"""Unit tests for RecordTypeOptional validator (parse_json_strings)."""

import pytest
from pydantic import ValidationError

from clarinet.models.record import RecordTypeOptional


class TestParseJsonStrings:
    """Tests for the parse_json_strings field_validator on RecordTypeOptional."""

    # --- data_schema ---

    def test_json_string_parsed_to_dict(self):
        """JSON string should be parsed into a dict."""
        model = RecordTypeOptional(data_schema='{"type": "object"}')
        assert model.data_schema == {"type": "object"}

    def test_dict_passed_through(self):
        """Dict value should pass through unchanged."""
        schema = {"type": "object", "properties": {"x": {"type": "string"}}}
        model = RecordTypeOptional(data_schema=schema)
        assert model.data_schema == schema

    def test_none_passed_through(self):
        """None should pass through unchanged."""
        model = RecordTypeOptional(data_schema=None)
        assert model.data_schema is None

    def test_empty_string_rejected_by_type_validation(self):
        """Empty string is skipped by JSON parser but rejected by dict type validation."""
        with pytest.raises(ValidationError, match="dict_type"):
            RecordTypeOptional(data_schema="")

    def test_invalid_json_raises_validation_error(self):
        """Invalid JSON string should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid JSON"):
            RecordTypeOptional(data_schema="not json at all")

    # --- slicer_script_args ---

    def test_slicer_args_json_string_parsed(self):
        """JSON string in slicer_script_args should be parsed."""
        model = RecordTypeOptional(slicer_script_args='{"arg": "val"}')
        assert model.slicer_script_args == {"arg": "val"}

    # --- slicer_result_validator_args ---

    def test_validator_args_json_string_parsed(self):
        """JSON string in slicer_result_validator_args should be parsed."""
        model = RecordTypeOptional(slicer_result_validator_args='{"check": "true"}')
        assert model.slicer_result_validator_args == {"check": "true"}

    # --- Full model validation ---

    def test_model_with_mixed_inputs(self):
        """Model should handle mix of dict and JSON string inputs."""
        model = RecordTypeOptional(
            data_schema={"type": "object"},
            slicer_script_args='{"arg": "val"}',
            description="updated",
        )
        assert model.data_schema == {"type": "object"}
        assert model.slicer_script_args == {"arg": "val"}
        assert model.description == "updated"

    def test_model_only_set_fields(self):
        """exclude_unset should correctly track which fields were explicitly set."""
        model = RecordTypeOptional(description="new desc")
        dumped = model.model_dump(exclude_unset=True)
        assert dumped == {"description": "new desc"}

    def test_model_exclude_unset_with_json_field(self):
        """JSON-parsed field should appear in exclude_unset dump."""
        model = RecordTypeOptional(data_schema='{"type": "object"}')
        dumped = model.model_dump(exclude_unset=True)
        assert "data_schema" in dumped
        assert dumped["data_schema"] == {"type": "object"}

    # --- ui_schema (formosh ui-schema) ---

    def test_ui_schema_json_string_parsed_to_dict(self):
        """JSON string for ui_schema should be parsed into a dict."""
        model = RecordTypeOptional(ui_schema='{"ui:order": ["a", "b"]}')
        assert model.ui_schema == {"ui:order": ["a", "b"]}

    def test_ui_schema_dict_passed_through(self):
        """Dict ui_schema should pass through unchanged."""
        ui = {"ui:order": ["x"], "x": {"ui:widget": "textarea"}}
        model = RecordTypeOptional(ui_schema=ui)
        assert model.ui_schema == ui

    def test_ui_schema_none_passed_through(self):
        """None ui_schema should pass through unchanged."""
        model = RecordTypeOptional(ui_schema=None)
        assert model.ui_schema is None

    def test_ui_schema_invalid_json_raises_validation_error(self):
        """Invalid JSON string for ui_schema should raise ValidationError."""
        with pytest.raises(ValidationError, match="Invalid JSON"):
            RecordTypeOptional(ui_schema="not json at all")

    def test_ui_schema_exclude_unset(self):
        """ui_schema appears in exclude_unset dump when explicitly set."""
        model = RecordTypeOptional(ui_schema={"ui:order": ["a"]})
        dumped = model.model_dump(exclude_unset=True)
        assert dumped == {"ui_schema": {"ui:order": ["a"]}}


class TestUiSchemaOnRecordTypeBase:
    """Tests that ui_schema flows through the inheritance hierarchy."""

    def test_base_defaults_to_none(self):
        """RecordTypeBase.ui_schema defaults to None."""
        from clarinet.models.record_type import RecordTypeBase

        model = RecordTypeBase(name="rt-a")
        assert model.ui_schema is None

    def test_create_accepts_ui_schema_dict(self):
        """RecordTypeCreate accepts a ui_schema dict."""
        from clarinet.models.record_type import RecordTypeCreate

        ui = {"ui:order": ["x"], "x": {"ui:widget": "textarea"}}
        model = RecordTypeCreate(name="rt-a", ui_schema=ui)
        assert model.ui_schema == ui

    def test_create_round_trip_via_model_dump(self):
        """ui_schema round-trips through model_dump on RecordTypeCreate."""
        from clarinet.models.record_type import RecordTypeCreate

        ui = {"ui:order": ["x"]}
        model = RecordTypeCreate(name="rt-a", data_schema={"type": "object"}, ui_schema=ui)
        dumped = model.model_dump()
        assert dumped["data_schema"] == {"type": "object"}
        assert dumped["ui_schema"] == ui
