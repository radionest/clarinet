"""Integration tests for TOML config bidirectional sync.

Uses real DB + tmp_path with real TOML files.
"""

import json

import pytest
import tomli_w
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from src.config.reconciler import reconcile_record_types
from src.config.toml_exporter import (
    delete_record_type_files,
    export_data_schema_sidecar,
    export_record_type_to_toml,
)
from src.models.file_schema import RecordTypeFileLink
from src.models.record import RecordType
from src.utils.config_loader import discover_config_files, load_record_config


def _write_toml(tmp_path, name: str, data: dict) -> None:
    """Write a TOML config file to tmp_path."""
    path = tmp_path / f"{name}.toml"
    path.write_text(tomli_w.dumps(data))


def _write_schema(tmp_path, name: str, schema: dict) -> None:
    """Write a schema sidecar JSON to tmp_path."""
    path = tmp_path / f"{name}.schema.json"
    path.write_text(json.dumps(schema))


@pytest.mark.asyncio
async def test_bootstrap_creates_from_toml(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """Write TOML to tmp_path → reconcile → verify DB."""
    _write_toml(
        tmp_path,
        "seg_markup",
        {"name": "seg_markup", "description": "Segmentation", "level": "SERIES"},
    )
    _write_schema(
        tmp_path,
        "seg_markup",
        {"type": "object", "properties": {"score": {"type": "number"}}},
    )

    # Load via standard config pipeline
    config_files = discover_config_files(str(tmp_path))
    config_props = []
    for cf in config_files:
        props = await load_record_config(cf)
        if props:
            config_props.append(props)

    result = await reconcile_record_types(config_props, test_session)
    assert "seg_markup" in result.created

    # Verify in DB
    stmt = select(RecordType).where(RecordType.name == "seg_markup")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.description == "Segmentation"
    assert rt.data_schema is not None


@pytest.mark.asyncio
async def test_bootstrap_updates_changed_toml(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """Modify TOML → reconcile → verify DB updated."""
    # First: create initial
    _write_toml(
        tmp_path,
        "seg_update",
        {"name": "seg_update", "description": "Original", "level": "SERIES"},
    )
    _write_schema(tmp_path, "seg_update", {"type": "object"})

    config_files = discover_config_files(str(tmp_path))
    props_list = [await load_record_config(cf) for cf in config_files]
    props_list = [p for p in props_list if p is not None]
    await reconcile_record_types(props_list, test_session)

    # Second: update TOML
    _write_toml(
        tmp_path,
        "seg_update",
        {"name": "seg_update", "description": "Updated", "level": "SERIES"},
    )
    _write_schema(tmp_path, "seg_update", {"type": "object"})

    config_files = discover_config_files(str(tmp_path))
    props_list = [await load_record_config(cf) for cf in config_files]
    props_list = [p for p in props_list if p is not None]
    result = await reconcile_record_types(props_list, test_session)
    assert "seg_update" in result.updated

    stmt = select(RecordType).where(RecordType.name == "seg_update")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.description == "Updated"


@pytest.mark.asyncio
async def test_export_record_type_to_toml(tmp_path) -> None:
    """Export RecordType → verify TOML written to disk."""
    rt = RecordType(
        name="export_test",
        description="Exported",
        level="SERIES",
        label="Test",
    )

    path = await export_record_type_to_toml(rt, tmp_path)
    assert path.exists()
    assert path.name == "export_test.toml"

    import tomllib

    content = tomllib.loads(path.read_text())
    assert content["name"] == "export_test"
    assert content["description"] == "Exported"


@pytest.mark.asyncio
async def test_export_data_schema_sidecar(tmp_path) -> None:
    """data_schema → {name}.schema.json written."""
    rt = RecordType(
        name="schema_test",
        description="Schema export",
        level="SERIES",
        data_schema={"type": "object", "properties": {"val": {"type": "integer"}}},
    )

    path = await export_data_schema_sidecar(rt, tmp_path)
    assert path is not None
    assert path.name == "schema_test.schema.json"

    content = json.loads(path.read_text())
    assert content["type"] == "object"


@pytest.mark.asyncio
async def test_delete_removes_files(tmp_path) -> None:
    """delete_record_type_files removes TOML and schema files."""
    # Create files
    (tmp_path / "del_target.toml").write_text("name = 'del_target'")
    (tmp_path / "del_target.schema.json").write_text("{}")

    deleted = await delete_record_type_files("del_target", tmp_path)
    assert len(deleted) == 2
    assert not (tmp_path / "del_target.toml").exists()
    assert not (tmp_path / "del_target.schema.json").exists()


@pytest.mark.asyncio
async def test_file_registry_round_trip(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """TOML → DB → TOML: verify lossless round-trip for file_registry."""
    file_def = {
        "name": "seg_mask",
        "pattern": "seg.nrrd",
        "role": "input",
        "required": True,
        "multiple": False,
    }
    _write_toml(
        tmp_path,
        "round_trip",
        {
            "name": "round_trip",
            "description": "Round trip test",
            "level": "SERIES",
            "file_registry": [file_def],
        },
    )
    _write_schema(tmp_path, "round_trip", {"type": "object"})

    # Load and reconcile
    config_files = discover_config_files(str(tmp_path))
    props_list = [await load_record_config(cf) for cf in config_files]
    props_list = [p for p in props_list if p is not None]
    await reconcile_record_types(props_list, test_session)

    # Fetch from DB with eager loading
    stmt = (
        select(RecordType)
        .where(RecordType.name == "round_trip")
        .options(
            selectinload(RecordType.file_links).selectinload(  # type: ignore[arg-type]
                RecordTypeFileLink.file_definition
            ),
        )
    )
    rt = (await test_session.execute(stmt)).scalar_one()
    file_registry = rt.get_file_registry()
    assert file_registry is not None
    assert len(file_registry) > 0

    # Export back to TOML
    export_dir = tmp_path / "export"
    await export_record_type_to_toml(rt, export_dir)

    import tomllib

    exported = tomllib.loads((export_dir / "round_trip.toml").read_text())
    assert "file_registry" in exported
    assert exported["file_registry"][0]["name"] == "seg_mask"
