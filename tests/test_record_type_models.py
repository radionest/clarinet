"""Unit tests for RecordTypeOptional validator (parse_json_strings)."""

import pytest
from pydantic import ValidationError

from src.models.record import RecordTypeOptional


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
