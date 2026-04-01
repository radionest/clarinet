"""Integration tests for Python config mode.

Uses real DB + tmp_path with real Python config files.
"""

import json
import textwrap

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from clarinet.config.python_loader import load_python_config
from clarinet.config.reconciler import reconcile_record_types
from clarinet.models.file_schema import RecordTypeFileLink
from clarinet.models.record import RecordType


def _write_files_catalog(tmp_path, content: str) -> None:
    """Write files_catalog.py to tmp_path."""
    (tmp_path / "files_catalog.py").write_text(textwrap.dedent(content))


def _write_record_types(tmp_path, content: str) -> None:
    """Write record_types.py to tmp_path."""
    (tmp_path / "record_types.py").write_text(textwrap.dedent(content))


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
        from files_catalog import seg_mask

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
        from files_catalog import master_model

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
        from files_catalog import patient_data

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
        from files_catalog import custom_file

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
