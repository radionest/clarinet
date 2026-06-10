"""Integration tests for TOML config bidirectional sync.

Uses real DB + tmp_path with real TOML files.
"""

import json

import pytest
import tomli_w
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.config.reconciler import reconcile_record_types
from clarinet.config.toml_exporter import (
    _LIST_FIELDS,
    _SCALAR_FIELDS,
    _TABLE_FIELDS,
    delete_record_type_files,
    export_data_schema_sidecar,
    export_record_type_to_toml,
)
from clarinet.models.file_schema import RecordTypeFileLink
from clarinet.models.record import RecordType, RecordTypeCreate
from clarinet.models.record_type import RecordTypeBase
from clarinet.utils.config_loader import discover_config_files, load_record_config


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
        "seg-markup",
        {"name": "seg-markup", "description": "Segmentation", "level": "SERIES"},
    )
    _write_schema(
        tmp_path,
        "seg-markup",
        {"type": "object", "properties": {"score": {"type": "number"}}},
    )

    # Load via standard config pipeline
    config_files = discover_config_files(str(tmp_path))
    config_items = []
    for cf in config_files:
        props = await load_record_config(cf)
        if props:
            config_items.append(RecordTypeCreate(**props))

    result = await reconcile_record_types(config_items, test_session)
    assert "seg-markup" in result.created

    # Verify in DB
    stmt = select(RecordType).where(RecordType.name == "seg-markup")
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
        "seg-update",
        {"name": "seg-update", "description": "Original", "level": "SERIES"},
    )
    _write_schema(tmp_path, "seg-update", {"type": "object"})

    config_files = discover_config_files(str(tmp_path))
    items = [
        RecordTypeCreate(**p)
        for cf in config_files
        if (p := await load_record_config(cf)) is not None
    ]
    await reconcile_record_types(items, test_session)

    # Second: update TOML
    _write_toml(
        tmp_path,
        "seg-update",
        {"name": "seg-update", "description": "Updated", "level": "SERIES"},
    )
    _write_schema(tmp_path, "seg-update", {"type": "object"})

    config_files = discover_config_files(str(tmp_path))
    items = [
        RecordTypeCreate(**p)
        for cf in config_files
        if (p := await load_record_config(cf)) is not None
    ]
    result = await reconcile_record_types(items, test_session)
    assert "seg-update" in result.updated

    stmt = select(RecordType).where(RecordType.name == "seg-update")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.description == "Updated"


@pytest.mark.asyncio
async def test_export_record_type_to_toml(tmp_path) -> None:
    """Export RecordType → verify TOML written to disk."""
    rt = RecordType(
        name="export-test",
        description="Exported",
        level="SERIES",
        label="Test",
    )

    path = await export_record_type_to_toml(rt, tmp_path)
    assert path.exists()
    assert path.name == "export-test.toml"

    import tomllib

    content = tomllib.loads(path.read_text())
    assert content["name"] == "export-test"
    assert content["description"] == "Exported"


@pytest.mark.asyncio
async def test_export_data_schema_sidecar(tmp_path) -> None:
    """data_schema → {name}.schema.json written."""
    rt = RecordType(
        name="schema-test",
        description="Schema export",
        level="SERIES",
        data_schema={"type": "object", "properties": {"val": {"type": "integer"}}},
    )

    path = await export_data_schema_sidecar(rt, tmp_path)
    assert path is not None
    assert path.name == "schema-test.schema.json"

    content = json.loads(path.read_text())
    assert content["type"] == "object"


@pytest.mark.asyncio
async def test_delete_removes_files(tmp_path) -> None:
    """delete_record_type_files removes TOML and schema files."""
    # Create files
    (tmp_path / "del_target.toml").write_text("name = 'del_target'")
    (tmp_path / "del_target.schema.json").write_text("{}")
    (tmp_path / "del_target.ui_schema.json").write_text("{}")

    deleted = await delete_record_type_files("del_target", tmp_path)
    assert len(deleted) == 3
    assert not (tmp_path / "del_target.toml").exists()
    assert not (tmp_path / "del_target.schema.json").exists()
    assert not (tmp_path / "del_target.ui_schema.json").exists()


@pytest.mark.asyncio
async def test_export_ui_schema_sidecar(tmp_path) -> None:
    """ui_schema → {name}.ui_schema.json written."""
    from clarinet.config.toml_exporter import export_ui_schema_sidecar

    rt = RecordType(
        name="ui-test",
        description="UI export",
        level="SERIES",
        ui_schema={"ui:order": ["x"], "x": {"ui:widget": "textarea"}},
    )

    path = await export_ui_schema_sidecar(rt, tmp_path)
    assert path is not None
    assert path.name == "ui-test.ui_schema.json"

    content = json.loads(path.read_text())
    assert content == {"ui:order": ["x"], "x": {"ui:widget": "textarea"}}


@pytest.mark.asyncio
async def test_export_ui_schema_sidecar_skips_empty(tmp_path) -> None:
    """ui_schema empty/None → no sidecar file written."""
    from clarinet.config.toml_exporter import export_ui_schema_sidecar

    rt = RecordType(name="empty-ui", level="SERIES", ui_schema={})
    assert await export_ui_schema_sidecar(rt, tmp_path) is None
    assert not (tmp_path / "empty-ui.ui_schema.json").exists()


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
        "round-trip",
        {
            "name": "round-trip",
            "description": "Round trip test",
            "level": "SERIES",
            "file_registry": [file_def],
        },
    )
    _write_schema(tmp_path, "round-trip", {"type": "object"})

    # Load and reconcile
    config_files = discover_config_files(str(tmp_path))
    items = [
        RecordTypeCreate(**p)
        for cf in config_files
        if (p := await load_record_config(cf)) is not None
    ]
    await reconcile_record_types(items, test_session)

    # Fetch from DB with eager loading
    stmt = (
        select(RecordType)
        .where(RecordType.name == "round-trip")
        .options(
            selectinload(RecordType.file_links).selectinload(  # type: ignore[arg-type]
                RecordTypeFileLink.file_definition
            ),
        )
    )
    rt = (await test_session.execute(stmt)).scalar_one()
    file_registry = rt.file_registry
    assert file_registry is not None
    assert len(file_registry) > 0

    # Export back to TOML
    export_dir = tmp_path / "export"
    await export_record_type_to_toml(rt, export_dir)

    import tomllib

    exported = tomllib.loads((export_dir / "round-trip.toml").read_text())
    assert "file_registry" in exported
    assert exported["file_registry"][0]["name"] == "seg_mask"


@pytest.mark.asyncio
async def test_export_includes_viewer_mode(tmp_path) -> None:
    """TOML export includes viewer_mode field."""
    import tomllib

    rt = RecordType(
        name="viewer-mode-test",
        level="SERIES",
        viewer_mode="all_series",
    )

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["viewer_mode"] == "all_series"


@pytest.mark.asyncio
async def test_export_includes_viewer_mode_default(tmp_path) -> None:
    """TOML export includes viewer_mode even with default value."""
    import tomllib

    rt = RecordType(name="viewer-default", level="SERIES")

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["viewer_mode"] == "single_series"


@pytest.mark.asyncio
async def test_export_includes_mask_patient_data(tmp_path) -> None:
    """TOML export includes mask_patient_data field."""
    import tomllib

    rt = RecordType(
        name="mask-test",
        level="SERIES",
        mask_patient_data=False,
    )

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["mask_patient_data"] is False


@pytest.mark.asyncio
async def test_export_includes_mask_patient_data_default(tmp_path) -> None:
    """TOML export includes mask_patient_data even with default value."""
    import tomllib

    rt = RecordType(name="mask-default", level="SERIES")

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["mask_patient_data"] is True


@pytest.mark.asyncio
async def test_export_includes_inherit_user_from_parent(tmp_path) -> None:
    """TOML export includes inherit_user_from_parent (explicit and default)."""
    import tomllib

    rt = RecordType(name="inherit-test", level="SERIES", inherit_user_from_parent=True)
    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["inherit_user_from_parent"] is True

    rt_default = RecordType(name="inherit-default", level="SERIES")
    path = await export_record_type_to_toml(rt_default, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["inherit_user_from_parent"] is False


@pytest.mark.asyncio
async def test_export_includes_editable_flags(tmp_path) -> None:
    """TOML export includes editable / edit_window_days; None window is omitted."""
    import tomllib

    rt = RecordType(name="editable-test", level="SERIES", editable=False, edit_window_days=14)
    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["editable"] is False
    assert content["edit_window_days"] == 14

    rt_default = RecordType(name="editable-default", level="SERIES")
    path = await export_record_type_to_toml(rt_default, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["editable"] is True
    assert "edit_window_days" not in content


@pytest.mark.asyncio
async def test_export_includes_unique_per_user(tmp_path) -> None:
    """TOML export includes unique_per_user field."""
    import tomllib

    rt = RecordType(
        name="unique-test",
        level="SERIES",
        unique_per_user=False,
    )

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["unique_per_user"] is False


@pytest.mark.asyncio
async def test_export_includes_parent_required(tmp_path) -> None:
    """TOML export includes parent_required field."""
    import tomllib

    rt = RecordType(
        name="parent-test",
        level="SERIES",
        parent_required=True,
    )

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["parent_required"] is True


@pytest.mark.asyncio
async def test_export_includes_constraint_flags_default(tmp_path) -> None:
    """TOML export includes unique_per_user/parent_required with defaults."""
    import tomllib

    rt = RecordType(name="flags-default", level="SERIES")

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["unique_per_user"] is True
    assert content["parent_required"] is False


@pytest.mark.asyncio
async def test_export_includes_hydrators_and_validators(tmp_path) -> None:
    """TOML export includes slicer_context_hydrators/data_validators arrays."""
    import tomllib

    rt = RecordType(
        name="lists-test",
        level="SERIES",
        slicer_context_hydrators=["seg_labels"],
        data_validators=["check_volume", "check_overlap"],
    )

    path = await export_record_type_to_toml(rt, tmp_path)
    content = tomllib.loads(path.read_text())
    assert content["slicer_context_hydrators"] == ["seg_labels"]
    assert content["data_validators"] == ["check_volume", "check_overlap"]

    rt_empty = RecordType(name="lists-empty", level="SERIES")
    path = await export_record_type_to_toml(rt_empty, tmp_path)
    content = tomllib.loads(path.read_text())
    assert "slicer_context_hydrators" not in content
    assert "data_validators" not in content


@pytest.mark.asyncio
async def test_constraint_flags_round_trip(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """TOML → DB → modified TOML → DB: constraint flags survive the update path."""
    _write_toml(
        tmp_path,
        "flags-trip",
        {
            "name": "flags-trip",
            "level": "SERIES",
            "unique_per_user": False,
            "parent_required": True,
        },
    )
    _write_schema(tmp_path, "flags-trip", {"type": "object"})

    config_files = discover_config_files(str(tmp_path))
    items = [
        RecordTypeCreate(**p)
        for cf in config_files
        if (p := await load_record_config(cf)) is not None
    ]
    await reconcile_record_types(items, test_session)

    stmt = select(RecordType).where(RecordType.name == "flags-trip")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.unique_per_user is False
    assert rt.parent_required is True

    # Flip both flags in TOML → reconcile must apply them on UPDATE
    _write_toml(
        tmp_path,
        "flags-trip",
        {
            "name": "flags-trip",
            "level": "SERIES",
            "unique_per_user": True,
            "parent_required": False,
        },
    )
    _write_schema(tmp_path, "flags-trip", {"type": "object"})
    test_session.expire_all()

    config_files = discover_config_files(str(tmp_path))
    items = [
        RecordTypeCreate(**p)
        for cf in config_files
        if (p := await load_record_config(cf)) is not None
    ]
    result = await reconcile_record_types(items, test_session)
    assert "flags-trip" in result.updated

    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.unique_per_user is True
    assert rt.parent_required is False


def test_exporter_covers_all_record_type_fields() -> None:
    """Drift guard: every RecordTypeBase field is exported or excluded here.

    A model field missing from the exporter tuples silently disappears from
    the rewritten .toml on API edits — unique_per_user, parent_required and
    inherit_user_from_parent all hit this before being added.
    """
    exported = set(_SCALAR_FIELDS) | set(_TABLE_FIELDS) | set(_LIST_FIELDS)
    intentionally_excluded = {"data_schema", "ui_schema"}  # sidecar-authoritative
    model_fields = set(RecordTypeBase.model_fields)
    assert model_fields - exported - intentionally_excluded == set()
