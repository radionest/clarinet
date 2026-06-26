"""
Unit tests for file pattern utilities.

This module tests the file pattern functions used for resolving
placeholders and matching filenames.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clarinet.files._patterns import (
    PLACEHOLDER_REGEX,
    fields_from,
    glob_file_paths,
)
from clarinet.files._template import RenderMode, render_template
from clarinet.models.file_schema import FileDefinitionRead, FileRole


def _render_for(pattern: str, record: MagicMock, parent: MagicMock | None = None) -> str:
    """Local helper replacing the deleted resolve_pattern(pattern, record, *parents)."""
    return render_template(pattern, fields_from(record, parent), mode=RenderMode.LENIENT)


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
    record.record_type.name = "ct-segmentation"
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


class TestFieldsFrom:
    """Tests for fields_from — replacement for the deleted resolve_record_field."""

    def test_resolve_simple_field(self, mock_record: MagicMock) -> None:
        """Simple top-level fields appear in the fields dict."""
        f = fields_from(mock_record)
        assert str(f["id"]) == "42"
        assert f["patient_id"] == "patient-456"
        assert f["study_uid"] == "1.2.3.4.5"

    def test_resolve_data_field(self, mock_record: MagicMock) -> None:
        """Nested data fields are accessible via the data sub-dict."""
        f = fields_from(mock_record)
        assert str(f["data"]["BIRADS_R"]) == "4"
        assert str(f["data"]["confidence"]) == "0.95"

    def test_resolve_record_type_field(self, mock_record: MagicMock) -> None:
        """record_type.name is accessible via the record_type sub-dict."""
        f = fields_from(mock_record)
        assert f["record_type"]["name"] == "ct-segmentation"

    def test_resolve_missing_field_renders_empty(self) -> None:
        """A missing top-level field renders to empty string in LENIENT mode."""
        record = MagicMock()
        record.id = 42
        record.user_id = None
        record.patient_id = "P"
        record.study_uid = None
        record.series_uid = None
        record.data = {}
        record.record_type = MagicMock()
        record.record_type.name = "t"
        f = fields_from(record)
        # {nonexistent} is not in the dict → renders to ""
        result = render_template("{nonexistent}", f, mode=RenderMode.LENIENT)
        assert result == ""

    def test_resolve_missing_nested_data_field(self, mock_record: MagicMock) -> None:
        """Missing nested data key renders to empty string."""
        f = fields_from(mock_record)
        result = render_template("{data.nonexistent}", f, mode=RenderMode.LENIENT)
        assert result == ""


class TestResolvePattern:
    """Tests for the new render_for / _render_for helper (replaces resolve_pattern)."""

    def test_static_pattern(self, mock_record: MagicMock) -> None:
        """Test that static patterns are unchanged."""
        result = _render_for("master_model.nrrd", mock_record)
        assert result == "master_model.nrrd"

    def test_single_placeholder(self, mock_record: MagicMock) -> None:
        """Test pattern with single placeholder."""
        result = _render_for("result_{id}.json", mock_record)
        assert result == "result_42.json"

    def test_multiple_placeholders(self, mock_record: MagicMock) -> None:
        """Test pattern with multiple placeholders."""
        result = _render_for("seg_{study_uid}_{id}.seg.nrrd", mock_record)
        assert result == "seg_1.2.3.4.5_42.seg.nrrd"

    def test_data_placeholder(self, mock_record: MagicMock) -> None:
        """Test pattern with data field placeholder."""
        result = _render_for("birads_{data.BIRADS_R}.txt", mock_record)
        assert result == "birads_4.txt"

    def test_record_type_placeholder(self, mock_record: MagicMock) -> None:
        """Test pattern with record_type field placeholder."""
        result = _render_for("{record_type.name}_output.nrrd", mock_record)
        assert result == "ct-segmentation_output.nrrd"

    def test_fallback_to_parent(self, mock_record: MagicMock) -> None:
        """Test that {user_id} resolves from parent when empty on record."""
        child = MagicMock()
        child.user_id = None
        child.id = 99
        child.data = {}
        child.patient_id = "P"
        child.study_uid = "S"
        child.series_uid = "SE"
        child.record_type = MagicMock()
        child.record_type.name = "child-type"

        result = _render_for("seg_{user_id}.nrrd", child, mock_record)
        assert result == "seg_user-123.nrrd"

    def test_record_takes_precedence_over_parent(self, mock_record: MagicMock) -> None:
        """Test that record value is used when both record and parent have the field."""
        parent = MagicMock()
        parent.user_id = "parent-user"
        parent.data = {}
        parent.record_type = MagicMock()
        parent.record_type.name = "parent-type"

        result = _render_for("seg_{user_id}.nrrd", mock_record, parent)
        assert result == "seg_user-123.nrrd"

    def test_no_parent_empty_field(self) -> None:
        """Test that empty field resolves to empty string when no parent given."""
        child = MagicMock()
        child.user_id = None
        child.id = 99
        child.patient_id = "P"
        child.study_uid = "S"
        child.series_uid = "SE"
        child.data = {}
        child.record_type = MagicMock()
        child.record_type.name = "child-type"

        result = _render_for("seg_{user_id}.nrrd", child)
        assert result == "seg_.nrrd"


class TestOriginType:
    """Tests for the {origin_type} virtual field."""

    def test_origin_type_resolves_from_parent(self, mock_record: MagicMock) -> None:
        """When parent is given, {origin_type} resolves to parent's type name."""
        child = MagicMock()
        child.record_type = MagicMock()
        child.record_type.name = "child-type"
        child.user_id = None
        child.id = 99
        child.patient_id = "P"
        child.study_uid = "S"
        child.series_uid = "SE"
        child.data = {}

        mock_record.record_type.name = "parent-type"

        result = _render_for("seg_{origin_type}.nrrd", child, mock_record)
        assert result == "seg_parent-type.nrrd"

    def test_origin_type_fallback_to_own(self) -> None:
        """Without parent, {origin_type} resolves to own record type name."""
        record = MagicMock()
        record.record_type = MagicMock()
        record.record_type.name = "my-type"
        record.user_id = None
        record.id = 1
        record.patient_id = "P"
        record.study_uid = "S"
        record.series_uid = "SE"
        record.data = {}

        result = _render_for("seg_{origin_type}.nrrd", record)
        assert result == "seg_my-type.nrrd"

    def test_origin_type_combined_with_regular(self, mock_record: MagicMock) -> None:
        """Pattern with both {origin_type} and {user_id} resolves correctly."""
        child = MagicMock()
        child.record_type = MagicMock()
        child.record_type.name = "child-type"
        child.user_id = "user-456"
        child.id = 99
        child.patient_id = "P"
        child.study_uid = "S"
        child.series_uid = "SE"
        child.data = {}

        mock_record.record_type.name = "parent-type"

        result = _render_for("segmentation_{origin_type}_{user_id}.seg.nrrd", child, mock_record)
        assert result == "segmentation_parent-type_user-456.seg.nrrd"

    def test_regular_fields_unaffected(self, mock_record: MagicMock) -> None:
        """{record_type.name} still prefers primary record (normal priority)."""
        parent = MagicMock()
        parent.record_type = MagicMock()
        parent.record_type.name = "parent-type"
        parent.user_id = "parent-user"
        parent.id = 99
        parent.patient_id = "P"
        parent.study_uid = "S"
        parent.series_uid = "SE"
        parent.data = {}

        result = _render_for("{record_type.name}_output.nrrd", mock_record, parent)
        assert result == "ct-segmentation_output.nrrd"


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
