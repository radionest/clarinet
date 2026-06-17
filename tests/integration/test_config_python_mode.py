"""Integration tests for Python config mode.

Uses real DB + tmp_path with real Python config files.
"""

import json
import textwrap

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.config.primitives import RecordDef
from clarinet.config.python_loader import load_python_config
from clarinet.config.reconciler import _COMPARED_FIELDS, reconcile_record_types
from clarinet.models.file_schema import RecordTypeFileLink
from clarinet.models.record import RecordType


def _write_files_catalog(tmp_path, content: str) -> None:
    """Write files_catalog.py to tmp_path."""
    (tmp_path / "files_catalog.py").write_text(textwrap.dedent(content))


def _write_record_types(tmp_path, content: str) -> None:
    """Write record_types.py to tmp_path."""
    (tmp_path / "record_types.py").write_text(textwrap.dedent(content))


@pytest.mark.asyncio
async def test_mask_patient_data_only_forwarded_when_explicit(tmp_path) -> None:
    """``mask_patient_data`` is omitted from ``RecordTypeCreate`` when not set in RecordDef.

    Convention: optional fields are propagated only when explicitly set in the
    Python config so the reconciler skips them and preserves DB state. Mirrors
    how every other optional field is handled in ``_to_record_type_create``.
    """
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        # Explicit False — must be forwarded
        clinical = RecordDef(name="clinical-rt", level="STUDY", mask_patient_data=False)
        # Default — must NOT be forwarded
        radiology = RecordDef(name="radiology-rt", level="STUDY")
        """,
    )

    config_items = await load_python_config(tmp_path)
    by_name = {item.name: item for item in config_items}

    # Explicit False is forwarded and visible in model_fields_set
    assert "mask_patient_data" in by_name["clinical-rt"].model_fields_set
    assert by_name["clinical-rt"].mask_patient_data is False

    # Unset stays absent so the reconciler skips comparison and preserves DB
    assert "mask_patient_data" not in by_name["radiology-rt"].model_fields_set


@pytest.mark.asyncio
async def test_inherit_user_from_parent_forwarded_when_explicit(tmp_path) -> None:
    """``inherit_user_from_parent`` propagates from RecordDef only when set."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        # Explicit True — must be forwarded
        child = RecordDef(name="child-rt", level="SERIES", inherit_user_from_parent=True)
        # Default — must NOT be forwarded
        plain = RecordDef(name="plain-rt", level="SERIES")
        """,
    )

    config_items = await load_python_config(tmp_path)
    by_name = {item.name: item for item in config_items}

    assert "inherit_user_from_parent" in by_name["child-rt"].model_fields_set
    assert by_name["child-rt"].inherit_user_from_parent is True

    assert "inherit_user_from_parent" not in by_name["plain-rt"].model_fields_set


@pytest.mark.asyncio
async def test_editable_flags_forwarded_when_explicit(tmp_path) -> None:
    """``editable`` / ``edit_window_days`` propagate from RecordDef only when set."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        # Explicit values — must be forwarded
        locked = RecordDef(name="locked-rt", level="SERIES", editable=False, edit_window_days=7)
        # Defaults — must NOT be forwarded
        plain = RecordDef(name="plain-rt", level="SERIES")
        """,
    )

    config_items = await load_python_config(tmp_path)
    by_name = {item.name: item for item in config_items}

    assert "editable" in by_name["locked-rt"].model_fields_set
    assert by_name["locked-rt"].editable is False
    assert "edit_window_days" in by_name["locked-rt"].model_fields_set
    assert by_name["locked-rt"].edit_window_days == 7

    assert "editable" not in by_name["plain-rt"].model_fields_set
    assert "edit_window_days" not in by_name["plain-rt"].model_fields_set


@pytest.mark.asyncio
async def test_unique_per_user_forwarded_when_explicit(tmp_path) -> None:
    """``unique_per_user`` propagates from RecordDef only when set."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        # Explicit False — must be forwarded
        shared = RecordDef(name="shared-rt", level="STUDY", unique_per_user=False)
        # Default — must NOT be forwarded
        plain = RecordDef(name="plain-rt", level="STUDY")
        """,
    )

    config_items = await load_python_config(tmp_path)
    by_name = {item.name: item for item in config_items}

    assert "unique_per_user" in by_name["shared-rt"].model_fields_set
    assert by_name["shared-rt"].unique_per_user is False

    assert "unique_per_user" not in by_name["plain-rt"].model_fields_set


def test_recorddef_exposes_exactly_synced_fields() -> None:
    """Drift sentinel: RecordDef fields must match the reconciler's synced set.

    ``_to_record_type_create`` maps RecordDef → RecordTypeCreate field-by-field,
    so any field the reconciler syncs (``_COMPARED_FIELDS``) must exist on
    RecordDef, and vice versa. This fails the moment the two diverge — e.g. a new
    column added to ``RecordTypeBase`` + ``_COMPARED_FIELDS`` but not mirrored
    onto RecordDef would silently be undriveable from Python config.
    """
    # name = identity (always forwarded, never "compared");
    # files = list[FileRef] mapped to file_registry via fileref_to_file_definition,
    #         synced by the M2M link diff rather than _COMPARED_FIELDS.
    special = {"name", "files"}
    recorddef_synced = set(RecordDef.model_fields) - special
    compared = set(_COMPARED_FIELDS)
    assert recorddef_synced == compared, (
        "RecordDef syncable fields drifted from reconciler _COMPARED_FIELDS — "
        f"RecordDef-only={recorddef_synced - compared}, "
        f"reconciler-only={compared - recorddef_synced}. "
        "Add the field to both RecordDef and _COMPARED_FIELDS (and forward it in "
        "_to_record_type_create), or extend `special` if it is a new identity / "
        "non-_COMPARED_FIELDS (e.g. M2M) field."
    )


# Every reconciler-synced field, set to a non-default value. Kept in lockstep
# with _COMPARED_FIELDS so a newly-synced field forces a sample here (and thus a
# real forwarding + value check). All values are plain dict/list/scalar so they
# survive the resolvers unchanged; str-enums (level, viewer_mode) compare equal
# to their value.
_FORWARD_SAMPLES: dict[str, object] = {
    "level": "PATIENT",
    "description": "every synced field set",
    "label": "All Fields",
    "role_name": "some-role",
    "min_records": 2,
    "max_records": 5,
    "slicer_script": "print('script')",
    "slicer_script_args": {"a": "b"},
    "slicer_result_validator": "print('validator')",
    "slicer_result_validator_args": {"c": "d"},
    "slicer_context_hydrators": ["hydrator_one"],
    "data_validators": ["validator_one"],
    "data_schema": {"type": "object"},
    "ui_schema": {"name": {"ui:widget": "text"}},
    "mask_patient_data": False,
    "unique_per_user": False,
    "parent_required": True,
    "inherit_user_from_parent": True,
    "editable": False,
    "edit_window_days": 7,
    "viewer_mode": "all_series",
    "allowed_viewers": ["ohif"],
}


@pytest.mark.asyncio
async def test_recorddef_forwards_every_synced_field(tmp_path) -> None:
    """Drift sentinel: every synced field set on a RecordDef must reach
    RecordTypeCreate *with its value intact*.

    Guards ``_to_record_type_create`` against silently dropping or mangling a
    field the reconciler syncs. A new ``_COMPARED_FIELDS`` entry that lacks a
    sample, is not forwarded, or is forwarded with the wrong value fails here.
    """
    # A newly-synced field with no sample below would slip the value check —
    # force the sample set to track _COMPARED_FIELDS exactly.
    assert set(_FORWARD_SAMPLES) == set(_COMPARED_FIELDS)

    kwargs_src = "\n".join(f"    {name}={value!r}," for name, value in _FORWARD_SAMPLES.items())
    _write_record_types(
        tmp_path,
        f"from clarinet.config.primitives import RecordDef\n\n"
        f'rt = RecordDef(\n    name="all-fields-rt",\n{kwargs_src}\n)\n',
    )

    [item] = await load_python_config(tmp_path)

    for name, expected in _FORWARD_SAMPLES.items():
        assert name in item.model_fields_set, f"_to_record_type_create dropped synced field: {name}"
        actual = getattr(item, name)
        assert actual == expected, f"{name} forwarded as {actual!r}, expected {expected!r}"


@pytest.mark.asyncio
async def test_bootstrap_loads_python_config(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """Write files_catalog.py + record_types.py → reconcile → verify DB."""
    _write_files_catalog(
        tmp_path,
        """\
        from clarinet.config.primitives import FileDef

        seg_mask = FileDef(pattern="seg.nrrd", level="SERIES", description="Segmentation mask")
        """,
    )
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef, FileRef
        from clarinet_plan.files_catalog import seg_mask

        lesion_seg = RecordDef(
            name="lesion-seg",
            description="Lesion segmentation",
            level="SERIES",
            files=[FileRef(seg_mask, "input")],
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].name == "lesion-seg"

    result = await reconcile_record_types(config_items, test_session)
    assert "lesion-seg" in result.created

    # Verify in DB
    stmt = select(RecordType).where(RecordType.name == "lesion-seg")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.description == "Lesion segmentation"


@pytest.mark.asyncio
async def test_file_refs_resolved(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """FileRef(file_obj) → FileDefinition in DB."""
    _write_files_catalog(
        tmp_path,
        """\
        from clarinet.config.primitives import FileDef

        master_model = FileDef(pattern="master.nrrd", level="SERIES", description="Master model")
        """,
    )
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef, FileRef
        from clarinet.models.file_schema import FileRole
        from clarinet_plan.files_catalog import master_model

        ai_analysis = RecordDef(
            name="ai-analysis",
            description="AI analysis task",
            level="SERIES",
            files=[FileRef(master_model, role=FileRole.OUTPUT, required=False)],
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    await reconcile_record_types(config_items, test_session)

    stmt = (
        select(RecordType)
        .where(RecordType.name == "ai-analysis")
        .options(
            selectinload(RecordType.file_links).selectinload(  # type: ignore[arg-type]
                RecordTypeFileLink.file_definition
            ),
        )
    )
    rt = (await test_session.execute(stmt)).scalar_one()
    file_registry = rt.file_registry
    assert file_registry is not None
    assert len(file_registry) == 1

    fd = file_registry[0]
    assert fd.name == "master_model"
    assert fd.pattern == "master.nrrd"
    assert fd.role == "output"
    assert fd.required is False


@pytest.mark.asyncio
async def test_schema_sidecar_loaded(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """{name}.schema.json loaded in Python mode."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        sidecar_type = RecordDef(
            name="sidecar-type",
            description="Type with sidecar schema",
            level="SERIES",
        )
        """,
    )
    # Write sidecar schema
    schema = {"type": "object", "properties": {"grade": {"type": "integer"}}}
    (tmp_path / "sidecar-type.schema.json").write_text(json.dumps(schema))

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].data_schema is not None
    assert config_items[0].data_schema["type"] == "object"


@pytest.mark.asyncio
async def test_ui_schema_inline_dict(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """RecordDef(ui_schema={...}) flows through to RecordTypeCreate."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        with_ui = RecordDef(
            name="with-ui",
            level="SERIES",
            data_schema={"type": "object"},
            ui_schema={"ui:order": ["a", "b"]},
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].ui_schema == {"ui:order": ["a", "b"]}


@pytest.mark.asyncio
async def test_ui_schema_sidecar_loaded(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """{name}.ui_schema.json loaded in Python mode when ui_schema absent."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        sidecar_ui = RecordDef(
            name="sidecar-ui",
            level="SERIES",
            data_schema={"type": "object"},
        )
        """,
    )
    ui = {"ui:order": ["x"], "x": {"ui:widget": "textarea"}}
    (tmp_path / "sidecar-ui.ui_schema.json").write_text(json.dumps(ui))

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].ui_schema == ui


@pytest.mark.asyncio
async def test_reconcile_updates_on_change(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """Modify Python file → re-reconcile → DB updated."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        mutable_type = RecordDef(
            name="mutable-type",
            description="Version 1",
            level="SERIES",
        )
        """,
    )
    (tmp_path / "mutable_type.schema.json").write_text('{"type": "object"}')

    config_items = await load_python_config(tmp_path)
    await reconcile_record_types(config_items, test_session)

    # Modify
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        mutable_type = RecordDef(
            name="mutable-type",
            description="Version 2",
            level="STUDY",
        )
        """,
    )
    (tmp_path / "mutable_type.schema.json").write_text('{"type": "object"}')

    # Re-reading a changed plan file means re-importing it — production does
    # this by re-activating the anchor at each app start. Simulate a restart so
    # the second load picks up Version 2 instead of the cached Version 1.
    from clarinet.config.plan_package import deactivate_plan_package

    deactivate_plan_package()

    config_items = await load_python_config(tmp_path)
    result = await reconcile_record_types(config_items, test_session)
    assert "mutable-type" in result.updated

    stmt = select(RecordType).where(RecordType.name == "mutable-type")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.description == "Version 2"
    assert rt.level == "STUDY"


@pytest.mark.asyncio
async def test_file_level_persisted(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """FileDef(level="PATIENT") → FileDefinition.level in DB."""
    _write_files_catalog(
        tmp_path,
        """\
        from clarinet.config.primitives import FileDef

        patient_data = FileDef(
            pattern="data.json",
            description="Patient-level data",
            level="PATIENT",
        )
        """,
    )
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef, FileRef
        from clarinet_plan.files_catalog import patient_data

        cross_level = RecordDef(
            name="cross-level",
            description="Cross-level test",
            level="SERIES",
            files=[FileRef(patient_data, "input")],
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    await reconcile_record_types(config_items, test_session)

    stmt = (
        select(RecordType)
        .where(RecordType.name == "cross-level")
        .options(
            selectinload(RecordType.file_links).selectinload(  # type: ignore[arg-type]
                RecordTypeFileLink.file_definition
            ),
        )
    )
    rt = (await test_session.execute(stmt)).scalar_one()
    file_registry = rt.file_registry
    assert len(file_registry) == 1
    assert file_registry[0].level == "PATIENT"


@pytest.mark.asyncio
async def test_python_mode_no_record_types_file(tmp_path) -> None:
    """Missing record_types.py → empty list."""
    result = await load_python_config(tmp_path)
    assert result == []


@pytest.mark.asyncio
async def test_role_alias(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """RecordDef(role='doctor_CT') maps to role_name."""
    from clarinet.models.user import UserRole

    # Role must exist before RecordType can reference it (FK constraint)
    test_session.add(UserRole(name="doctor_CT"))
    await test_session.commit()

    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import RecordDef

        with_role = RecordDef(
            name="with-role",
            description="Test role alias",
            level="STUDY",
            role="doctor_CT",
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].role_name == "doctor_CT"

    result = await reconcile_record_types(config_items, test_session)
    assert "with-role" in result.created

    stmt = select(RecordType).where(RecordType.name == "with-role")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.role_name == "doctor_CT"


@pytest.mark.asyncio
async def test_single_file_mode(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """FileDef + RecordDef in same record_types.py (no files_catalog.py)."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import FileDef, FileRef, RecordDef

        my_file = FileDef(pattern="output.nrrd", level="SERIES", description="Output file")

        single_file_type = RecordDef(
            name="single-file-type",
            description="Single-file mode test",
            level="SERIES",
            files=[FileRef(my_file, "output")],
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].file_registry is not None
    assert len(config_items[0].file_registry) == 1
    assert config_items[0].file_registry[0].name == "my_file"


@pytest.mark.asyncio
async def test_backward_compat_old_names(
    tmp_path,
) -> None:
    """Old names File/RecordTypeDef still work via aliases."""
    _write_record_types(
        tmp_path,
        """\
        from clarinet.config.primitives import File, RecordTypeDef, FileRef
        from clarinet.models.file_schema import FileRole

        old_file = File(pattern="old.nrrd", level="SERIES", description="Old name")

        old_type = RecordTypeDef(
            name="old-type",
            description="Backward compat test",
            level="SERIES",
            files=[FileRef(old_file, role=FileRole.INPUT)],
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].name == "old-type"
    assert config_items[0].file_registry is not None
    assert config_items[0].file_registry[0].name == "old_file"


@pytest.mark.asyncio
async def test_custom_record_types_path(
    tmp_path,
) -> None:
    """record_types.py loaded from subdirectory via config_record_types_file."""
    (tmp_path / "definitions").mkdir()
    (tmp_path / "definitions" / "record_types.py").write_text(
        textwrap.dedent("""\
        from clarinet.config.primitives import RecordDef

        custom_path_type = RecordDef(
            name="custom-path-type",
            description="Loaded from subdirectory",
            level="SERIES",
        )
        """)
    )

    from clarinet.settings import settings

    orig = settings.config_record_types_file
    settings.config_record_types_file = "definitions/record_types.py"
    try:
        config_items = await load_python_config(tmp_path)
    finally:
        settings.config_record_types_file = orig

    assert len(config_items) == 1
    assert config_items[0].name == "custom-path-type"


@pytest.mark.asyncio
async def test_custom_files_catalog_path(
    test_session: AsyncSession,
    tmp_path,
) -> None:
    """files_catalog.py loaded from subdirectory via config_files_catalog_file."""
    (tmp_path / "definitions").mkdir()
    (tmp_path / "definitions" / "files_catalog.py").write_text(
        textwrap.dedent("""\
        from clarinet.config.primitives import FileDef

        custom_file = FileDef(pattern="custom.nrrd", level="SERIES", description="Custom file")
        """)
    )
    (tmp_path / "definitions" / "record_types.py").write_text(
        textwrap.dedent("""\
        from clarinet.config.primitives import RecordDef, FileRef
        from clarinet_plan.definitions.files_catalog import custom_file

        catalog_test = RecordDef(
            name="catalog-test",
            description="Uses custom catalog path",
            level="SERIES",
            files=[FileRef(custom_file, "input")],
        )
        """)
    )

    from clarinet.settings import settings

    orig_rt = settings.config_record_types_file
    orig_fc = settings.config_files_catalog_file
    settings.config_record_types_file = "definitions/record_types.py"
    settings.config_files_catalog_file = "definitions/files_catalog.py"
    try:
        config_items = await load_python_config(tmp_path)
    finally:
        settings.config_record_types_file = orig_rt
        settings.config_files_catalog_file = orig_fc

    assert len(config_items) == 1
    assert config_items[0].file_registry is not None
    assert config_items[0].file_registry[0].name == "custom_file"
