"""Unit tests for config_loader module."""

import json
from pathlib import Path

import pytest

from clarinet.utils.config_loader import (
    discover_config_files,
    load_record_config,
)


class TestDiscoverConfigFiles:
    """Tests for discover_config_files function."""

    def test_toml_only(self, tmp_path: Path) -> None:
        """Test discovery with only TOML files."""
        (tmp_path / "task1.toml").write_text("[record]\nname = 'task1'")
        (tmp_path / "task2.toml").write_text("[record]\nname = 'task2'")

        result = discover_config_files(str(tmp_path))

        assert len(result) == 2
        assert all(p.suffix == ".toml" for p in result)
        assert result[0].stem == "task1"
        assert result[1].stem == "task2"

    def test_json_only(self, tmp_path: Path) -> None:
        """Test discovery with only JSON files."""
        (tmp_path / "task1.json").write_text('{"name": "task1"}')
        (tmp_path / "task2.json").write_text('{"name": "task2"}')

        result = discover_config_files(str(tmp_path))

        assert len(result) == 2
        assert all(p.suffix == ".json" for p in result)
        assert result[0].stem == "task1"
        assert result[1].stem == "task2"

    def test_mixed_formats(self, tmp_path: Path) -> None:
        """Test discovery with both TOML and JSON for different stems."""
        (tmp_path / "task1.toml").write_text("[record]\nname = 'task1'")
        (tmp_path / "task2.json").write_text('{"name": "task2"}')
        (tmp_path / "task3.toml").write_text("[record]\nname = 'task3'")

        result = discover_config_files(str(tmp_path))

        assert len(result) == 3
        stems = [p.stem for p in result]
        assert "task1" in stems
        assert "task2" in stems
        assert "task3" in stems

    def test_toml_takes_precedence(self, tmp_path: Path) -> None:
        """Test TOML takes precedence when both exist for same stem."""
        (tmp_path / "task1.toml").write_text("[record]\nname = 'task1'")
        (tmp_path / "task1.json").write_text('{"name": "task1"}')

        result = discover_config_files(str(tmp_path))

        assert len(result) == 1
        assert result[0].suffix == ".toml"
        assert result[0].stem == "task1"

    def test_schema_json_excluded(self, tmp_path: Path) -> None:
        """Test *.schema.json files are excluded."""
        (tmp_path / "task1.toml").write_text("[record]\nname = 'task1'")
        (tmp_path / "task1.schema.json").write_text('{"type": "object"}')
        (tmp_path / "other.schema.json").write_text('{"type": "object"}')

        result = discover_config_files(str(tmp_path))

        assert len(result) == 1
        assert result[0].stem == "task1"
        assert result[0].suffix == ".toml"

    def test_file_registry_excluded(self, tmp_path: Path) -> None:
        """Test file_registry.json is excluded."""
        (tmp_path / "task1.toml").write_text("[record]\nname = 'task1'")
        (tmp_path / "file_registry.json").write_text('{"files": []}')

        result = discover_config_files(str(tmp_path))

        assert len(result) == 1
        assert result[0].stem == "task1"

    def test_file_registry_toml_excluded(self, tmp_path: Path) -> None:
        """Test file_registry.toml is excluded."""
        (tmp_path / "task1.toml").write_text("[record]\nname = 'task1'")
        (tmp_path / "file_registry.toml").write_text("[seg]\npattern = 'seg.nrrd'")

        result = discover_config_files(str(tmp_path))

        assert len(result) == 1
        assert result[0].stem == "task1"

    def test_suffix_filter(self, tmp_path: Path) -> None:
        """Test suffix_filter only includes stems containing filter string."""
        (tmp_path / "seg_lung.toml").write_text("[record]\nname = 'seg_lung'")
        (tmp_path / "seg_liver.toml").write_text("[record]\nname = 'seg_liver'")
        (tmp_path / "volume.toml").write_text("[record]\nname = 'volume'")

        result = discover_config_files(str(tmp_path), suffix_filter="seg")

        assert len(result) == 2
        stems = [p.stem for p in result]
        assert "seg_lung" in stems
        assert "seg_liver" in stems
        assert "volume" not in stems

    def test_empty_folder(self, tmp_path: Path) -> None:
        """Test empty folder returns empty list."""
        result = discover_config_files(str(tmp_path))

        assert result == []

    def test_nonexistent_folder(self, tmp_path: Path) -> None:
        """Test nonexistent folder returns empty list."""
        nonexistent = tmp_path / "does_not_exist"

        result = discover_config_files(str(nonexistent))

        assert result == []


class TestResolveFileReferences:
    """Tests for _resolve_file_references behavior via load_record_config."""

    @pytest.mark.asyncio
    async def test_py_slicer_script_resolved(self, tmp_path: Path) -> None:
        """Test .py slicer_script reference is read and inlined."""
        script_content = "print('Hello from slicer')"
        (tmp_path / "script.py").write_text(script_content)

        config = {
            "record": {"name": "test"},
            "slicer_script": "script.py",
            "data_schema": {"type": "object"},
        }
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["slicer_script"] == script_content

    @pytest.mark.asyncio
    async def test_py_validator_resolved(self, tmp_path: Path) -> None:
        """Test .py slicer_result_validator reference is read and inlined."""
        validator_content = "def validate(x): return True"
        (tmp_path / "validator.py").write_text(validator_content)

        config = {
            "record": {"name": "test"},
            "slicer_result_validator": "validator.py",
            "data_schema": {"type": "object"},
        }
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["slicer_result_validator"] == validator_content

    @pytest.mark.asyncio
    async def test_inline_script_unchanged(self, tmp_path: Path) -> None:
        """Test non-.py string preserved as-is."""
        inline_script = "console.log('inline');"
        config = {
            "record": {"name": "test"},
            "slicer_script": inline_script,
            "data_schema": {"type": "object"},
        }
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["slicer_script"] == inline_script

    @pytest.mark.asyncio
    async def test_missing_py_raises_file_not_found(self, tmp_path: Path) -> None:
        """Test FileNotFoundError raised when .py file doesn't exist."""
        config = {
            "record": {"name": "test"},
            "slicer_script": "missing.py",
            "data_schema": {"type": "object"},
        }
        (tmp_path / "task.json").write_text(json.dumps(config))

        with pytest.raises(FileNotFoundError):
            await load_record_config(tmp_path / "task.json")

    @pytest.mark.asyncio
    async def test_no_slicer_fields_unchanged(self, tmp_path: Path) -> None:
        """Test dict without script fields passes through."""
        config = {
            "record": {"name": "test"},
            "other_field": "value",
            "data_schema": {"type": "object"},
        }
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["other_field"] == "value"
        assert "slicer_script" not in result


class TestResolveDataSchema:
    """Tests for _resolve_data_schema behavior via load_record_config."""

    @pytest.mark.asyncio
    async def test_json_ref_loaded(self, tmp_path: Path) -> None:
        """Test string .json reference is parsed."""
        schema = {"type": "object", "properties": {"value": {"type": "number"}}}
        (tmp_path / "schema.json").write_text(json.dumps(schema))

        config = {"record": {"name": "test"}, "data_schema": "schema.json"}
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["data_schema"] == schema

    @pytest.mark.asyncio
    async def test_inline_dict_preserved(self, tmp_path: Path) -> None:
        """Test dict value stays as dict."""
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        config = {"record": {"name": "test"}, "data_schema": schema}
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["data_schema"] == schema

    @pytest.mark.asyncio
    async def test_sidecar_fallback(self, tmp_path: Path) -> None:
        """Test {stem}.schema.json loaded when data_schema absent."""
        schema = {"type": "object", "properties": {"area": {"type": "number"}}}
        (tmp_path / "task.schema.json").write_text(json.dumps(schema))

        config = {"record": {"name": "test"}}
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["data_schema"] == schema

    @pytest.mark.asyncio
    async def test_legacy_result_schema_renamed(self, tmp_path: Path) -> None:
        """Test result_schema renamed to data_schema."""
        schema = {"type": "object", "properties": {"result": {"type": "string"}}}
        config = {"record": {"name": "test"}, "result_schema": schema}
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["data_schema"] == schema
        assert "result_schema" not in result

    @pytest.mark.asyncio
    async def test_no_schema_returns_none(self, tmp_path: Path) -> None:
        """Test returns None when no schema found."""
        config = {"record": {"name": "test"}}
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is None


class TestLoadRecordConfig:
    """Tests for load_record_config function."""

    @pytest.mark.asyncio
    async def test_full_toml_with_py_ref_and_sidecar(self, tmp_path: Path) -> None:
        """Test end-to-end TOML loading with .py reference and sidecar schema."""
        script_content = "# Slicer script\nprint('Processing')"
        (tmp_path / "process.py").write_text(script_content)

        schema = {"type": "object", "properties": {"volume": {"type": "number"}}}
        (tmp_path / "task.schema.json").write_text(json.dumps(schema))

        toml_content = """\
name = "volume_calc"
description = "Calculate lung volume"
level = "SERIES"
slicer_script = "process.py"
"""
        (tmp_path / "task.toml").write_text(toml_content)

        result = await load_record_config(tmp_path / "task.toml")

        assert result is not None
        assert result["name"] == "volume_calc"
        assert result["slicer_script"] == script_content
        assert result["data_schema"] == schema

    @pytest.mark.asyncio
    async def test_full_json_backward_compat(self, tmp_path: Path) -> None:
        """Test existing JSON still works."""
        config = {
            "record": {"name": "Segmentation", "description": "Liver segmentation"},
            "slicer_script": "inline script content",
            "data_schema": {"type": "object", "properties": {"mask": {"type": "string"}}},
        }
        (tmp_path / "task.json").write_text(json.dumps(config))

        result = await load_record_config(tmp_path / "task.json")

        assert result is not None
        assert result["record"]["name"] == "Segmentation"
        assert result["slicer_script"] == "inline script content"
        assert result["data_schema"]["properties"]["mask"]["type"] == "string"

    @pytest.mark.asyncio
    async def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        """Test raises ValueError for unsupported extensions."""
        (tmp_path / "task.xml").write_text("<config></config>")

        with pytest.raises(ValueError, match="Unsupported config format"):
            await load_record_config(tmp_path / "task.xml")

    @pytest.mark.asyncio
    async def test_toml_with_files_array(self, tmp_path: Path) -> None:
        """Test [[files]] in TOML produces correct list[dict]."""
        toml_content = """
[record]
name = "Multi-file Task"

[[files]]
key = "input_ct"
label = "Input CT Scan"
accept = ".nii.gz"

[[files]]
key = "mask"
label = "Segmentation Mask"
accept = ".nii.gz"

[data_schema]
type = "object"
"""
        (tmp_path / "task.toml").write_text(toml_content)

        result = await load_record_config(tmp_path / "task.toml")

        assert result is not None
        assert "files" in result
        assert len(result["files"]) == 2
        assert result["files"][0]["key"] == "input_ct"
        assert result["files"][0]["label"] == "Input CT Scan"
        assert result["files"][1]["key"] == "mask"
        assert result["files"][1]["label"] == "Segmentation Mask"
