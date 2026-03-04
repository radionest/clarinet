"""
Unit tests for RecordFileAccessor service.

Tests attribute-based file access for singular and collection files.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.models.file_schema import FileDefinition, FileRole
from src.services.file_accessor import RecordFileAccessor, get_file_accessor


@pytest.fixture
def mock_record() -> MagicMock:
    """Create a mock RecordRead for testing."""
    record = MagicMock()
    record.id = 42
    record.user_id = "user-123"
    record.patient_id = "patient-456"
    record.study_uid = "1.2.3.4.5"
    record.series_uid = "1.2.3.4.5.6"
    record.data = {"BIRADS_R": 4}
    record.working_folder = "/tmp/test_working"
    record.record_type = MagicMock()
    record.record_type.file_registry = [
        FileDefinition(
            name="lung_mask",
            pattern="lung_mask.seg.nrrd",
            role=FileRole.INPUT,
        ),
        FileDefinition(
            name="user_segmentation",
            pattern="lesions_{user_id}.seg.nrrd",
            multiple=True,
            role=FileRole.INPUT,
        ),
        FileDefinition(
            name="consensus",
            pattern="lesions_consensus.seg.nrrd",
            role=FileRole.OUTPUT,
        ),
        FileDefinition(
            name="ai_result",
            pattern="ai_result_{id}.nrrd",
            role=FileRole.INTERMEDIATE,
            required=False,
        ),
    ]
    return record


class TestRecordFileAccessor:
    """Tests for RecordFileAccessor class."""

    def test_singular_file_access(self, mock_record: MagicMock) -> None:
        """Test accessing a singular file returns a Path."""
        accessor = RecordFileAccessor(mock_record, working_folder="/data/study")
        path = accessor.lung_mask
        assert isinstance(path, Path)
        assert path == Path("/data/study/lung_mask.seg.nrrd")

    def test_singular_file_with_placeholder(self, mock_record: MagicMock) -> None:
        """Test singular file with placeholder resolves correctly."""
        accessor = RecordFileAccessor(mock_record, working_folder="/data/study")
        path = accessor.ai_result
        assert isinstance(path, Path)
        assert path == Path("/data/study/ai_result_42.nrrd")

    def test_collection_file_access(self, mock_record: MagicMock, tmp_path: Path) -> None:
        """Test accessing a collection file returns list of Paths via glob."""
        # Create matching files
        (tmp_path / "lesions_user-123.seg.nrrd").touch()
        (tmp_path / "lesions_user-456.seg.nrrd").touch()
        (tmp_path / "lesions_user-789.seg.nrrd").touch()
        # Non-matching file
        (tmp_path / "other_file.nrrd").touch()

        accessor = RecordFileAccessor(mock_record, working_folder=tmp_path)
        paths = accessor.user_segmentation
        assert isinstance(paths, list)
        assert len(paths) == 3
        assert all(isinstance(p, Path) for p in paths)
        assert all("lesions_" in p.name and ".seg.nrrd" in p.name for p in paths)

    def test_collection_empty_when_no_matches(self, mock_record: MagicMock, tmp_path: Path) -> None:
        """Test collection returns empty list when no files match."""
        accessor = RecordFileAccessor(mock_record, working_folder=tmp_path)
        paths = accessor.user_segmentation
        assert paths == []

    def test_attribute_error_for_unknown_name(self, mock_record: MagicMock) -> None:
        """Test accessing unknown file definition raises AttributeError."""
        accessor = RecordFileAccessor(mock_record, working_folder="/data")
        with pytest.raises(AttributeError, match="No file definition 'nonexistent'"):
            accessor.nonexistent  # noqa: B018

    def test_path_for_creates_dirs(self, mock_record: MagicMock, tmp_path: Path) -> None:
        """Test path_for creates parent directories."""
        accessor = RecordFileAccessor(mock_record, working_folder=tmp_path)
        path = accessor.path_for("consensus")
        assert path == tmp_path / "lesions_consensus.seg.nrrd"
        assert path.parent.exists()

    def test_available_returns_all_names(self, mock_record: MagicMock) -> None:
        """Test available() returns all file definition names."""
        accessor = RecordFileAccessor(mock_record, working_folder="/data")
        names = accessor.available()
        assert set(names) == {"lung_mask", "user_segmentation", "consensus", "ai_result"}

    def test_uses_record_working_folder_by_default(self, mock_record: MagicMock) -> None:
        """Test that accessor uses record.working_folder when no override is given."""
        accessor = RecordFileAccessor(mock_record)
        path = accessor.lung_mask
        assert str(path).startswith("/tmp/test_working")

    def test_dict_file_definitions_handled(self, mock_record: MagicMock) -> None:
        """Test that dict-format file definitions (JSON deserialized) are handled."""
        mock_record.record_type.file_registry = [
            {"name": "test_file", "pattern": "test.nrrd", "role": "input"},
        ]
        accessor = RecordFileAccessor(mock_record, working_folder="/data")
        path = accessor.test_file
        assert path == Path("/data/test.nrrd")


class TestGetFileAccessor:
    """Tests for get_file_accessor factory function."""

    def test_factory_creates_accessor(self, mock_record: MagicMock) -> None:
        """Test factory function returns a RecordFileAccessor."""
        accessor = get_file_accessor(mock_record, working_folder="/data")
        assert isinstance(accessor, RecordFileAccessor)

    def test_factory_with_path_override(self, mock_record: MagicMock) -> None:
        """Test factory with Path override."""
        accessor = get_file_accessor(mock_record, working_folder=Path("/custom/path"))
        assert accessor.lung_mask == Path("/custom/path/lung_mask.seg.nrrd")
