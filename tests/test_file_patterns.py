"""
Unit tests for file pattern utilities.

This module tests the file pattern functions used for resolving
placeholders and matching filenames.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clarinet.models.file_schema import FileDefinitionRead, FileRole
from clarinet.utils.file_patterns import (
    PLACEHOLDER_REGEX,
    glob_file_paths,
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


class TestGlobFilePaths:
    """Tests for glob_file_paths function."""

    def test_glob_matches_files(self, tmp_path: Path) -> None:
        """Test globbing finds matching files."""
        (tmp_path / "seg_user-A.nrrd").touch()
        (tmp_path / "seg_user-B.nrrd").touch()
        (tmp_path / "other.nrrd").touch()

        fd = FileDefinitionRead(
            name="segs",
            pattern="seg_{user_id}.nrrd",
            multiple=True,
            role=FileRole.INPUT,
        )
        paths = glob_file_paths(fd, tmp_path)
        assert len(paths) == 2
        assert all("seg_" in p.name for p in paths)

    def test_glob_empty_when_no_matches(self, tmp_path: Path) -> None:
        """Test globbing returns empty list when no files match."""
        fd = FileDefinitionRead(
            name="segs",
            pattern="seg_{user_id}.nrrd",
            multiple=True,
            role=FileRole.INPUT,
        )
        paths = glob_file_paths(fd, tmp_path)
        assert paths == []

    def test_glob_returns_sorted(self, tmp_path: Path) -> None:
        """Test that glob results are sorted."""
        (tmp_path / "seg_c.nrrd").touch()
        (tmp_path / "seg_a.nrrd").touch()
        (tmp_path / "seg_b.nrrd").touch()

        fd = FileDefinitionRead(
            name="segs",
            pattern="seg_{user_id}.nrrd",
            multiple=True,
            role=FileRole.INPUT,
        )
        paths = glob_file_paths(fd, tmp_path)
        assert [p.name for p in paths] == ["seg_a.nrrd", "seg_b.nrrd", "seg_c.nrrd"]
