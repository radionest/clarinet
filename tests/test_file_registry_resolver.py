"""Tests for file registry resolver module."""

import json
import textwrap
from pathlib import Path

import pytest

from src.exceptions.domain import ValidationError
from src.models.file_schema import FileRole
from src.utils.file_registry_resolver import (
    FileReference,
    load_project_file_registry,
    resolve_file_references,
    resolve_task_files,
)


class TestFileReference:
    """Tests for FileReference model."""

    def test_defaults(self) -> None:
        """Test FileReference with default values."""
        ref = FileReference(name="test_file")
        assert ref.name == "test_file"
        assert ref.role == FileRole.OUTPUT
        assert ref.required is True

    def test_custom_values(self) -> None:
        """Test FileReference with custom values."""
        ref = FileReference(name="input_file", role=FileRole.INPUT, required=False)
        assert ref.name == "input_file"
        assert ref.role == FileRole.INPUT
        assert ref.required is False


class TestLoadProjectFileRegistry:
    """Tests for load_project_file_registry function."""

    @pytest.mark.asyncio
    async def test_load_existing_registry(self, tmp_path: Path) -> None:
        """Test loading an existing file registry."""
        registry_data = {
            "ct_scan": {
                "pattern": "*.dcm",
                "description": "CT scan DICOM files",
                "multiple": True,
            },
            "segmentation": {
                "pattern": "seg.nrrd",
                "description": "Segmentation mask",
                "multiple": False,
            },
        }
        registry_path = tmp_path / "file_registry.json"
        registry_path.write_text(json.dumps(registry_data))

        result = await load_project_file_registry(str(tmp_path))

        assert result is not None
        assert len(result) == 2
        assert "ct_scan" in result
        assert result["ct_scan"]["pattern"] == "*.dcm"
        assert result["ct_scan"]["description"] == "CT scan DICOM files"
        assert result["ct_scan"]["multiple"] is True
        assert "segmentation" in result
        assert result["segmentation"]["pattern"] == "seg.nrrd"

    @pytest.mark.asyncio
    async def test_load_nonexistent_registry(self, tmp_path: Path) -> None:
        """Test loading when file_registry.json doesn't exist."""
        result = await load_project_file_registry(str(tmp_path))
        assert result is None

    @pytest.mark.asyncio
    async def test_load_empty_registry(self, tmp_path: Path) -> None:
        """Test loading an empty file registry."""
        registry_path = tmp_path / "file_registry.json"
        registry_path.write_text(json.dumps({}))

        result = await load_project_file_registry(str(tmp_path))

        assert result is not None
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_load_toml_registry(self, tmp_path: Path) -> None:
        """Test loading a TOML file registry."""
        toml_content = textwrap.dedent("""\
            [ct_scan]
            pattern = "*.dcm"
            description = "CT scan DICOM files"
            multiple = true

            [segmentation]
            pattern = "seg.nrrd"
            description = "Segmentation mask"
            multiple = false
        """)
        (tmp_path / "file_registry.toml").write_text(toml_content)

        result = await load_project_file_registry(str(tmp_path))

        assert result is not None
        assert len(result) == 2
        assert "ct_scan" in result
        assert result["ct_scan"]["pattern"] == "*.dcm"
        assert result["ct_scan"]["description"] == "CT scan DICOM files"
        assert result["ct_scan"]["multiple"] is True
        assert "segmentation" in result
        assert result["segmentation"]["pattern"] == "seg.nrrd"

    @pytest.mark.asyncio
    async def test_toml_takes_precedence_over_json(self, tmp_path: Path) -> None:
        """Test TOML file registry takes precedence over JSON."""
        json_data = {"from_json": {"pattern": "*.json", "description": "JSON source"}}
        (tmp_path / "file_registry.json").write_text(json.dumps(json_data))

        toml_content = textwrap.dedent("""\
            [from_toml]
            pattern = "*.toml"
            description = "TOML source"
        """)
        (tmp_path / "file_registry.toml").write_text(toml_content)

        result = await load_project_file_registry(str(tmp_path))

        assert result is not None
        assert "from_toml" in result
        assert "from_json" not in result


class TestResolveFileReferences:
    """Tests for resolve_file_references function."""

    @pytest.fixture
    def sample_registry(self) -> dict[str, dict[str, str | bool]]:
        """Sample file registry for testing."""
        return {
            "ct_scan": {
                "pattern": "*.dcm",
                "description": "CT scan DICOM files",
                "multiple": True,
            },
            "segmentation": {
                "pattern": "seg.nrrd",
                "description": "Segmentation mask",
                "multiple": False,
            },
            "report": {
                "pattern": "report.pdf",
            },
        }

    def test_resolve_single_reference(
        self, sample_registry: dict[str, dict[str, str | bool]]
    ) -> None:
        """Test resolving a single file reference."""
        files = [{"name": "ct_scan", "role": "input", "required": True}]

        resolved = resolve_file_references(files, sample_registry)

        assert len(resolved) == 1
        assert resolved[0].name == "ct_scan"
        assert resolved[0].pattern == "*.dcm"
        assert resolved[0].description == "CT scan DICOM files"
        assert resolved[0].required is True
        assert resolved[0].multiple is True
        assert resolved[0].role == FileRole.INPUT

    def test_resolve_multiple_references(
        self, sample_registry: dict[str, dict[str, str | bool]]
    ) -> None:
        """Test resolving multiple file references."""
        files = [
            {"name": "ct_scan", "role": "input", "required": True},
            {"name": "segmentation", "role": "output", "required": False},
            {"name": "report", "role": "output", "required": True},
        ]

        resolved = resolve_file_references(files, sample_registry)

        assert len(resolved) == 3
        assert resolved[0].name == "ct_scan"
        assert resolved[0].role == FileRole.INPUT
        assert resolved[1].name == "segmentation"
        assert resolved[1].role == FileRole.OUTPUT
        assert resolved[1].required is False
        assert resolved[2].name == "report"
        assert resolved[2].pattern == "report.pdf"
        assert resolved[2].description is None

    def test_resolve_unknown_reference(
        self, sample_registry: dict[str, dict[str, str | bool]]
    ) -> None:
        """Test resolving an unknown file reference raises ValidationError."""
        files = [{"name": "unknown_file", "role": "input", "required": True}]

        with pytest.raises(ValidationError) as exc_info:
            resolve_file_references(files, sample_registry)

        assert "File reference 'unknown_file' not found" in str(exc_info.value)
        assert "Available: ct_scan, segmentation, report" in str(exc_info.value)

    def test_resolve_with_defaults(self, sample_registry: dict[str, dict[str, str | bool]]) -> None:
        """Test resolving references with default values."""
        files = [{"name": "ct_scan"}]

        resolved = resolve_file_references(files, sample_registry)

        assert len(resolved) == 1
        assert resolved[0].name == "ct_scan"
        assert resolved[0].role == FileRole.OUTPUT
        assert resolved[0].required is True

    def test_resolve_empty_list(self, sample_registry: dict[str, dict[str, str | bool]]) -> None:
        """Test resolving empty file list."""
        files: list[dict[str, str]] = []

        resolved = resolve_file_references(files, sample_registry)

        assert len(resolved) == 0


class TestResolveTaskFiles:
    """Tests for resolve_task_files function."""

    @pytest.fixture
    def sample_registry(self) -> dict[str, dict[str, str | bool]]:
        """Sample file registry for testing."""
        return {
            "ct_scan": {
                "pattern": "*.dcm",
                "description": "CT scan DICOM files",
                "multiple": True,
            },
            "segmentation": {
                "pattern": "seg.nrrd",
                "description": "Segmentation mask",
                "multiple": False,
            },
        }

    def test_resolve_files_key(self, sample_registry: dict[str, dict[str, str | bool]]) -> None:
        """Test resolving task with 'files' key."""
        props = {
            "name": "Test Task",
            "files": [
                {"name": "ct_scan", "role": "input", "required": True},
                {"name": "segmentation", "role": "output", "required": False},
            ],
        }

        resolved = resolve_task_files(props, sample_registry)

        assert "files" not in resolved
        assert "file_registry" in resolved
        assert len(resolved["file_registry"]) == 2
        assert resolved["file_registry"][0]["name"] == "ct_scan"
        assert resolved["file_registry"][0]["pattern"] == "*.dcm"
        assert resolved["file_registry"][1]["name"] == "segmentation"
        assert resolved["name"] == "Test Task"

    def test_file_registry_key_passes_through(
        self, sample_registry: dict[str, dict[str, str | bool]]
    ) -> None:
        """Test task with existing 'file_registry' key passes through unchanged."""
        props = {
            "name": "Test Task",
            "file_registry": [
                {
                    "name": "custom_file",
                    "pattern": "*.txt",
                    "role": "input",
                    "required": True,
                    "multiple": False,
                }
            ],
        }

        resolved = resolve_task_files(props, sample_registry)

        assert resolved == props
        assert "file_registry" in resolved
        assert resolved["file_registry"][0]["name"] == "custom_file"

    def test_both_keys_raises_error(
        self, sample_registry: dict[str, dict[str, str | bool]]
    ) -> None:
        """Test task with both 'files' and 'file_registry' raises ValidationError."""
        props = {
            "name": "Test Task",
            "files": [{"name": "ct_scan", "role": "input"}],
            "file_registry": [{"name": "custom_file", "pattern": "*.txt"}],
        }

        with pytest.raises(ValidationError) as exc_info:
            resolve_task_files(props, sample_registry)

        assert "must not have both 'files' and 'file_registry'" in str(exc_info.value)

    def test_no_files_key_passes_through(
        self, sample_registry: dict[str, dict[str, str | bool]]
    ) -> None:
        """Test task without 'files' or 'file_registry' keys passes through."""
        props = {
            "name": "Test Task",
            "description": "A simple task",
        }

        resolved = resolve_task_files(props, sample_registry)

        assert resolved == props
        assert "files" not in resolved
        assert "file_registry" not in resolved

    def test_files_without_registry_raises_error(self) -> None:
        """Test task with 'files' but no registry raises ValidationError."""
        props = {
            "name": "Test Task",
            "files": [{"name": "ct_scan", "role": "input"}],
        }

        with pytest.raises(ValidationError) as exc_info:
            resolve_task_files(props, None)

        assert "Task uses 'files' references but no project file_registry.toml/.json" in str(
            exc_info.value
        )

    def test_no_files_with_no_registry(self) -> None:
        """Test task without 'files' key works even when registry is None."""
        props = {
            "name": "Test Task",
            "file_registry": [
                {
                    "name": "custom_file",
                    "pattern": "*.txt",
                    "role": "input",
                    "required": True,
                    "multiple": False,
                }
            ],
        }

        resolved = resolve_task_files(props, None)

        assert resolved == props

    def test_resolve_preserves_other_properties(
        self, sample_registry: dict[str, dict[str, str | bool]]
    ) -> None:
        """Test that resolving files preserves other task properties."""
        props = {
            "name": "Test Task",
            "description": "A test task",
            "timeout": 3600,
            "files": [{"name": "ct_scan", "role": "input"}],
            "extra_prop": "value",
        }

        resolved = resolve_task_files(props, sample_registry)

        assert resolved["name"] == "Test Task"
        assert resolved["description"] == "A test task"
        assert resolved["timeout"] == 3600
        assert resolved["extra_prop"] == "value"
        assert "files" not in resolved
        assert "file_registry" in resolved
