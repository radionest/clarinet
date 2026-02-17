"""
Unit tests for file pattern utilities.

This module tests the file pattern functions used for resolving
placeholders and matching filenames.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.utils.file_patterns import (
    PLACEHOLDER_REGEX,
    find_matching_file,
    generate_filename,
    match_filename,
    resolve_pattern,
    resolve_record_field,
)


@pytest.fixture
def mock_record() -> MagicMock:
    """Create a mock Record for testing."""
    record = MagicMock()
    record.id = 42
    record.user_id = "user-123"
    record.patient_id = "patient-456"
    record.study_uid = "1.2.3.4.5"
    record.series_uid = "1.2.3.4.5.6"
    record.data = {"BIRADS_R": 4, "confidence": 0.95, "nested": {"field": "value"}}
    record.record_type = MagicMock()
    record.record_type.name = "ct_segmentation"
    record.record_type.level = "SERIES"
    return record


class TestPlaceholderRegex:
    """Tests for PLACEHOLDER_REGEX pattern."""

    def test_matches_simple_placeholder(self) -> None:
        """Test matching simple placeholders."""
        matches = PLACEHOLDER_REGEX.findall("{id}")
        assert matches == ["id"]

    def test_matches_dotted_placeholder(self) -> None:
        """Test matching dotted placeholders like {data.field}."""
        matches = PLACEHOLDER_REGEX.findall("{data.BIRADS_R}")
        assert matches == ["data.BIRADS_R"]

    def test_matches_multiple_placeholders(self) -> None:
        """Test matching multiple placeholders in one string."""
        matches = PLACEHOLDER_REGEX.findall("seg_{study_uid}_{id}.nrrd")
        assert matches == ["study_uid", "id"]

    def test_no_match_without_braces(self) -> None:
        """Test that text without braces doesn't match."""
        matches = PLACEHOLDER_REGEX.findall("static_name.nrrd")
        assert matches == []


class TestResolveRecordField:
    """Tests for resolve_record_field function."""

    def test_resolve_simple_field(self, mock_record: MagicMock) -> None:
        """Test resolving simple record fields."""
        assert resolve_record_field(mock_record, "id") == "42"
        assert resolve_record_field(mock_record, "patient_id") == "patient-456"
        assert resolve_record_field(mock_record, "study_uid") == "1.2.3.4.5"

    def test_resolve_data_field(self, mock_record: MagicMock) -> None:
        """Test resolving nested data fields."""
        assert resolve_record_field(mock_record, "data.BIRADS_R") == "4"
        assert resolve_record_field(mock_record, "data.confidence") == "0.95"

    def test_resolve_record_type_field(self, mock_record: MagicMock) -> None:
        """Test resolving record_type fields."""
        assert resolve_record_field(mock_record, "record_type.name") == "ct_segmentation"

    def test_resolve_missing_field(self) -> None:
        """Test resolving non-existent field returns empty string."""
        # Use a real object without the field instead of MagicMock
        # (MagicMock auto-creates attributes)

        class FakeRecord:
            id = 42

        record = FakeRecord()
        assert resolve_record_field(record, "nonexistent") == ""  # type: ignore[arg-type]

    def test_resolve_missing_nested_field(self, mock_record: MagicMock) -> None:
        """Test resolving non-existent nested field returns empty string."""
        assert resolve_record_field(mock_record, "data.nonexistent") == ""


class TestResolvePattern:
    """Tests for resolve_pattern function."""

    def test_static_pattern(self, mock_record: MagicMock) -> None:
        """Test that static patterns are unchanged."""
        result = resolve_pattern("master_model.nrrd", mock_record)
        assert result == "master_model.nrrd"

    def test_single_placeholder(self, mock_record: MagicMock) -> None:
        """Test pattern with single placeholder."""
        result = resolve_pattern("result_{id}.json", mock_record)
        assert result == "result_42.json"

    def test_multiple_placeholders(self, mock_record: MagicMock) -> None:
        """Test pattern with multiple placeholders."""
        result = resolve_pattern("seg_{study_uid}_{id}.seg.nrrd", mock_record)
        assert result == "seg_1.2.3.4.5_42.seg.nrrd"

    def test_data_placeholder(self, mock_record: MagicMock) -> None:
        """Test pattern with data field placeholder."""
        result = resolve_pattern("birads_{data.BIRADS_R}.txt", mock_record)
        assert result == "birads_4.txt"

    def test_record_type_placeholder(self, mock_record: MagicMock) -> None:
        """Test pattern with record_type field placeholder."""
        result = resolve_pattern("{record_type.name}_output.nrrd", mock_record)
        assert result == "ct_segmentation_output.nrrd"


class TestMatchFilename:
    """Tests for match_filename function."""

    def test_exact_match(self, mock_record: MagicMock) -> None:
        """Test exact filename match."""
        assert match_filename("result_42.json", "result_{id}.json", mock_record) is True

    def test_no_match(self, mock_record: MagicMock) -> None:
        """Test filename that doesn't match."""
        assert match_filename("result_99.json", "result_{id}.json", mock_record) is False

    def test_static_match(self, mock_record: MagicMock) -> None:
        """Test static filename match."""
        assert match_filename("master_model.nrrd", "master_model.nrrd", mock_record) is True

    def test_static_no_match(self, mock_record: MagicMock) -> None:
        """Test static filename no match."""
        assert match_filename("other.nrrd", "master_model.nrrd", mock_record) is False


class TestFindMatchingFile:
    """Tests for find_matching_file function."""

    def test_file_found(self, mock_record: MagicMock, tmp_path: Path) -> None:
        """Test finding existing file."""
        # Create test file
        test_file = tmp_path / "result_42.json"
        test_file.touch()

        result = find_matching_file(tmp_path, "result_{id}.json", mock_record)
        assert result == "result_42.json"

    def test_file_not_found(self, mock_record: MagicMock, tmp_path: Path) -> None:
        """Test when file doesn't exist."""
        result = find_matching_file(tmp_path, "result_{id}.json", mock_record)
        assert result is None

    def test_directory_not_exists(self, mock_record: MagicMock) -> None:
        """Test when directory doesn't exist."""
        non_existent = Path("/nonexistent/directory")
        result = find_matching_file(non_existent, "result_{id}.json", mock_record)
        assert result is None

    def test_static_file_found(self, mock_record: MagicMock, tmp_path: Path) -> None:
        """Test finding static filename."""
        test_file = tmp_path / "master_model.nrrd"
        test_file.touch()

        result = find_matching_file(tmp_path, "master_model.nrrd", mock_record)
        assert result == "master_model.nrrd"


class TestGenerateFilename:
    """Tests for generate_filename function."""

    def test_generate_with_id(self, mock_record: MagicMock) -> None:
        """Test generating filename with ID."""
        result = generate_filename("seg_{id}.seg.nrrd", mock_record)
        assert result == "seg_42.seg.nrrd"

    def test_generate_static(self, mock_record: MagicMock) -> None:
        """Test generating static filename."""
        result = generate_filename("master_model.nrrd", mock_record)
        assert result == "master_model.nrrd"

    def test_generate_complex(self, mock_record: MagicMock) -> None:
        """Test generating filename with multiple placeholders."""
        result = generate_filename("{record_type.name}_{patient_id}_{id}.json", mock_record)
        assert result == "ct_segmentation_patient-456_42.json"
