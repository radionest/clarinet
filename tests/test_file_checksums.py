"""
Unit tests for file checksum utilities.

Tests SHA256 computation and change detection.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.models.file_schema import FileDefinitionRead, FileRole
from src.utils.file_checksums import (
    checksums_changed,
    compute_checksums,
    compute_file_checksum,
)


@pytest.fixture
def mock_record() -> MagicMock:
    """Create a mock RecordRead for testing."""
    record = MagicMock()
    record.id = 42
    record.user_id = "user-123"
    record.patient_id = "patient-456"
    record.study_uid = "1.2.3.4.5"
    record.series_uid = "1.2.3.4.5.6"
    record.data = {}
    record.working_folder = "/tmp/test"
    record.record_type = MagicMock()
    record.record_type.file_registry = [
        FileDefinitionRead(
            name="single_file",
            pattern="result.nrrd",
            role=FileRole.OUTPUT,
        ),
        FileDefinitionRead(
            name="user_segs",
            pattern="seg_{user_id}.nrrd",
            multiple=True,
            role=FileRole.INPUT,
        ),
    ]
    return record


class TestComputeFileChecksum:
    """Tests for compute_file_checksum function."""

    @pytest.mark.asyncio
    async def test_checksum_of_existing_file(self, tmp_path: Path) -> None:
        """Test computing checksum of an existing file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        result = await compute_file_checksum(test_file)
        assert result is not None
        assert len(result) == 64  # SHA256 hex length

    @pytest.mark.asyncio
    async def test_checksum_of_missing_file(self, tmp_path: Path) -> None:
        """Test computing checksum of a missing file returns None."""
        result = await compute_file_checksum(tmp_path / "nonexistent.txt")
        assert result is None

    @pytest.mark.asyncio
    async def test_same_content_same_checksum(self, tmp_path: Path) -> None:
        """Test that same content produces same checksum."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("identical content")
        file2.write_text("identical content")

        checksum1 = await compute_file_checksum(file1)
        checksum2 = await compute_file_checksum(file2)
        assert checksum1 == checksum2

    @pytest.mark.asyncio
    async def test_different_content_different_checksum(self, tmp_path: Path) -> None:
        """Test that different content produces different checksum."""
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("content A")
        file2.write_text("content B")

        checksum1 = await compute_file_checksum(file1)
        checksum2 = await compute_file_checksum(file2)
        assert checksum1 != checksum2


class TestComputeChecksums:
    """Tests for compute_checksums function."""

    @pytest.mark.asyncio
    async def test_computes_singular_file_checksums(
        self, mock_record: MagicMock, tmp_path: Path
    ) -> None:
        """Test computing checksums for singular files."""
        (tmp_path / "result.nrrd").write_text("data")

        checksums = await compute_checksums(
            mock_record.record_type.file_registry, mock_record, tmp_path
        )

        assert "single_file" in checksums
        assert len(checksums["single_file"]) == 64

    @pytest.mark.asyncio
    async def test_computes_collection_file_checksums(
        self, mock_record: MagicMock, tmp_path: Path
    ) -> None:
        """Test computing checksums for collection files."""
        (tmp_path / "seg_user-A.nrrd").write_text("data A")
        (tmp_path / "seg_user-B.nrrd").write_text("data B")

        checksums = await compute_checksums(
            mock_record.record_type.file_registry, mock_record, tmp_path
        )

        # Collection keys use "name:filename" format
        collection_keys = [k for k in checksums if k.startswith("user_segs:")]
        assert len(collection_keys) == 2

    @pytest.mark.asyncio
    async def test_skips_missing_files(self, mock_record: MagicMock, tmp_path: Path) -> None:
        """Test that missing files are skipped in checksums."""
        # Don't create any files
        checksums = await compute_checksums(
            mock_record.record_type.file_registry, mock_record, tmp_path
        )

        assert len(checksums) == 0


class TestChecksumsChanged:
    """Tests for checksums_changed function."""

    def test_detects_new_files(self) -> None:
        """Test detecting new files."""
        old: dict[str, str] = {}
        new = {"file1": "abc123"}
        changed = checksums_changed(old, new)
        assert changed == {"file1"}

    def test_detects_changed_files(self) -> None:
        """Test detecting changed files."""
        old = {"file1": "abc123"}
        new = {"file1": "def456"}
        changed = checksums_changed(old, new)
        assert changed == {"file1"}

    def test_no_changes(self) -> None:
        """Test no changes when checksums match."""
        old = {"file1": "abc123", "file2": "def456"}
        new = {"file1": "abc123", "file2": "def456"}
        changed = checksums_changed(old, new)
        assert changed == set()

    def test_handles_none_old(self) -> None:
        """Test that None old checksums are treated as empty."""
        changed = checksums_changed(None, {"file1": "abc123"})
        assert changed == {"file1"}

    def test_mixed_changes(self) -> None:
        """Test mix of new, changed, and unchanged files."""
        old = {"unchanged": "aaa", "changed": "bbb"}
        new = {"unchanged": "aaa", "changed": "ccc", "new_file": "ddd"}
        changed = checksums_changed(old, new)
        assert changed == {"changed", "new_file"}
