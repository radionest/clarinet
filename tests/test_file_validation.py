"""
Unit tests for file validation service.

This module tests the FileValidator class and related validation
functions for checking file existence against patterns.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.models.file_schema import FileDefinition
from src.services.file_validation import (
    FileValidationError,
    FileValidationResult,
    FileValidator,
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
    record.data = {"BIRADS_R": 4, "confidence": 0.95}
    return record


@pytest.fixture
def mock_record_type() -> MagicMock:
    """Create a mock RecordType for testing."""
    record_type = MagicMock()
    record_type.name = "ct_segmentation"
    record_type.input_files = [
        FileDefinition(name="ct_scan", pattern="ct_scan.nrrd", required=True),
        FileDefinition(
            name="mask", pattern="mask_{id}.nrrd", required=False, description="Optional mask"
        ),
    ]
    record_type.output_files = [
        FileDefinition(name="segmentation", pattern="seg_{id}.seg.nrrd", required=True),
    ]
    return record_type


@pytest.fixture
def validator(mock_record_type: MagicMock) -> FileValidator:
    """Create FileValidator instance."""
    return FileValidator(mock_record_type)


class TestFileValidationError:
    """Tests for FileValidationError dataclass."""

    def test_create_error(self) -> None:
        """Test creating a validation error."""
        error = FileValidationError(
            file_name="ct_scan",
            error_type="missing",
            message="Required file 'ct_scan' not found",
        )
        assert error.file_name == "ct_scan"
        assert error.error_type == "missing"
        assert "not found" in error.message


class TestFileValidationResult:
    """Tests for FileValidationResult dataclass."""

    def test_valid_result(self) -> None:
        """Test creating a valid result."""
        result = FileValidationResult(
            valid=True,
            matched_files={"ct_scan": "ct_scan.nrrd"},
        )
        assert result.valid is True
        assert result.errors == []
        assert result.matched_files == {"ct_scan": "ct_scan.nrrd"}

    def test_invalid_result(self) -> None:
        """Test creating an invalid result with errors."""
        error = FileValidationError(
            file_name="ct_scan",
            error_type="missing",
            message="Required file 'ct_scan' not found",
        )
        result = FileValidationResult(
            valid=False,
            errors=[error],
        )
        assert result.valid is False
        assert len(result.errors) == 1
        assert result.matched_files == {}


class TestFileValidator:
    """Tests for FileValidator class."""

    def test_validate_files_all_found(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validation when all required files exist."""
        # Create required files
        (tmp_path / "ct_scan.nrrd").touch()
        (tmp_path / "mask_42.nrrd").touch()

        result = validator.validate_files(
            mock_record,
            validator.record_type.input_files,
            tmp_path,
        )

        assert result.valid is True
        assert len(result.errors) == 0
        assert "ct_scan" in result.matched_files
        assert result.matched_files["ct_scan"] == "ct_scan.nrrd"
        assert "mask" in result.matched_files
        assert result.matched_files["mask"] == "mask_42.nrrd"

    def test_validate_files_required_missing(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validation when required file is missing."""
        # Don't create ct_scan.nrrd

        result = validator.validate_files(
            mock_record,
            validator.record_type.input_files,
            tmp_path,
        )

        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].file_name == "ct_scan"
        assert result.errors[0].error_type == "missing"

    def test_validate_files_optional_missing(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validation when only optional file is missing."""
        # Create required file only
        (tmp_path / "ct_scan.nrrd").touch()
        # Don't create optional mask file

        result = validator.validate_files(
            mock_record,
            validator.record_type.input_files,
            tmp_path,
        )

        assert result.valid is True
        assert len(result.errors) == 0
        assert "ct_scan" in result.matched_files
        assert "mask" not in result.matched_files

    def test_validate_files_empty_definitions(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validation with empty file definitions."""
        result = validator.validate_files(mock_record, None, tmp_path)

        assert result.valid is True
        assert len(result.errors) == 0
        assert result.matched_files == {}

    def test_validate_files_with_dict_definitions(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validation with dict file definitions (JSON deserialized)."""
        # Create required file
        (tmp_path / "ct_scan.nrrd").touch()

        # Use dict format as if deserialized from JSON
        dict_definitions = [
            {"name": "ct_scan", "pattern": "ct_scan.nrrd", "required": True},
        ]

        result = validator.validate_files(mock_record, dict_definitions, tmp_path)  # type: ignore[arg-type]

        assert result.valid is True
        assert "ct_scan" in result.matched_files

    def test_validate_input_files(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validate_input_files method."""
        (tmp_path / "ct_scan.nrrd").touch()

        result = validator.validate_input_files(mock_record, tmp_path)

        assert result.valid is True
        assert "ct_scan" in result.matched_files

    def test_validate_output_files(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validate_output_files method."""
        (tmp_path / "seg_42.seg.nrrd").touch()

        result = validator.validate_output_files(mock_record, tmp_path)

        assert result.valid is True
        assert "segmentation" in result.matched_files
        assert result.matched_files["segmentation"] == "seg_42.seg.nrrd"

    def test_validate_output_files_missing(
        self,
        validator: FileValidator,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validate_output_files when file is missing."""
        # Don't create the output file

        result = validator.validate_output_files(mock_record, tmp_path)

        assert result.valid is False
        assert len(result.errors) == 1
        assert result.errors[0].file_name == "segmentation"


class TestFileValidatorEdgeCases:
    """Edge case tests for FileValidator."""

    def test_multiple_required_missing(
        self,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validation when multiple required files are missing."""
        record_type = MagicMock()
        record_type.input_files = [
            FileDefinition(name="file1", pattern="file1.nrrd", required=True),
            FileDefinition(name="file2", pattern="file2.nrrd", required=True),
            FileDefinition(name="file3", pattern="file3.nrrd", required=True),
        ]

        validator = FileValidator(record_type)
        result = validator.validate_files(
            mock_record,
            record_type.input_files,
            tmp_path,
        )

        assert result.valid is False
        assert len(result.errors) == 3

    def test_dynamic_pattern_with_data_field(
        self,
        mock_record: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Test validation with data field in pattern."""
        record_type = MagicMock()
        record_type.input_files = [
            FileDefinition(name="birads_file", pattern="birads_{data.BIRADS_R}.txt", required=True),
        ]

        # Create file with resolved name
        (tmp_path / "birads_4.txt").touch()

        validator = FileValidator(record_type)
        result = validator.validate_files(
            mock_record,
            record_type.input_files,
            tmp_path,
        )

        assert result.valid is True
        assert result.matched_files["birads_file"] == "birads_4.txt"
