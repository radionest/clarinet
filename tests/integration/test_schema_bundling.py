"""Integration: cross-file ``$defs`` bundling through the config loaders."""

import json
import textwrap
from pathlib import Path

import pytest

from clarinet.config.python_loader import load_python_config
from clarinet.utils.config_loader import load_record_config


def _write(p: Path, obj: object) -> None:
    p.write_text(json.dumps(obj), encoding="utf-8")


@pytest.mark.asyncio
async def test_python_mode_json_path_bundles_external_def(tmp_path: Path) -> None:
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    _write(
        schemas / "_common.schema.json",
        {"$defs": {"Grade": {"type": "string", "enum": ["a", "b"]}}},
    )
    _write(
        schemas / "lesion.schema.json",
        {"type": "object", "properties": {"grade": {"$ref": "_common.schema.json#/$defs/Grade"}}},
    )
    (tmp_path / "record_types.py").write_text(
        textwrap.dedent(
            """\
            from clarinet.config.primitives import RecordDef

            lesion = RecordDef(name="lesion", level="SERIES", data_schema="schemas/lesion.schema.json")
            """
        )
    )

    items = await load_python_config(tmp_path)
    schema = {i.name: i for i in items}["lesion"].data_schema

    assert schema["properties"]["grade"] == {"$ref": "#/$defs/Grade"}
    assert schema["$defs"]["Grade"]["enum"] == ["a", "b"]


@pytest.mark.asyncio
async def test_python_mode_sidecar_bundles_external_def(tmp_path: Path) -> None:
    _write(tmp_path / "_common.schema.json", {"$defs": {"Grade": {"type": "string"}}})
    _write(
        tmp_path / "lesion.schema.json",
        {"type": "object", "properties": {"grade": {"$ref": "_common.schema.json#/$defs/Grade"}}},
    )
    (tmp_path / "record_types.py").write_text(
        textwrap.dedent(
            """\
            from clarinet.config.primitives import RecordDef

            lesion = RecordDef(name="lesion", level="SERIES")
            """
        )
    )

    items = await load_python_config(tmp_path)
    schema = {i.name: i for i in items}["lesion"].data_schema

    assert schema["properties"]["grade"] == {"$ref": "#/$defs/Grade"}
    assert "Grade" in schema["$defs"]


@pytest.mark.asyncio
async def test_config_loader_bundles_external_def(tmp_path: Path) -> None:
    _write(tmp_path / "_common.schema.json", {"$defs": {"Grade": {"type": "string"}}})
    _write(
        tmp_path / "lesion.schema.json",
        {"type": "object", "properties": {"grade": {"$ref": "_common.schema.json#/$defs/Grade"}}},
    )
    _write(
        tmp_path / "lesion.json",
        {"name": "lesion", "level": "SERIES", "data_schema": "lesion.schema.json"},
    )

    props = await load_record_config(tmp_path / "lesion.json")

    assert props is not None
    schema = props["data_schema"]
    assert schema["properties"]["grade"] == {"$ref": "#/$defs/Grade"}
    assert "Grade" in schema["$defs"]


@pytest.mark.asyncio
async def test_config_loader_sidecar_bundles_external_def(tmp_path: Path) -> None:
    _write(tmp_path / "_common.schema.json", {"$defs": {"Grade": {"type": "string"}}})
    _write(
        tmp_path / "lesion.schema.json",
        {"type": "object", "properties": {"grade": {"$ref": "_common.schema.json#/$defs/Grade"}}},
    )
    # No data_schema in the config → loader resolves the `{stem}.schema.json` sidecar.
    _write(tmp_path / "lesion.json", {"name": "lesion", "level": "SERIES"})

    props = await load_record_config(tmp_path / "lesion.json")

    assert props is not None
    schema = props["data_schema"]
    assert schema["properties"]["grade"] == {"$ref": "#/$defs/Grade"}
    assert "Grade" in schema["$defs"]
