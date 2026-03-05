"""Integration tests for Python config mode.

Uses real DB + tmp_path with real Python config files.
"""

import json
import textwrap

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from src.config.python_loader import load_python_config
from src.config.reconciler import reconcile_record_types
from src.models.file_schema import RecordTypeFileLink
from src.models.record import RecordType


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
        from src.config.primitives import File

        seg_mask = File(pattern="seg.nrrd", description="Segmentation mask")
        """,
    )
    _write_record_types(
        tmp_path,
        """\
        from src.config.primitives import RecordTypeDef, FileRef
        from src.models.file_schema import FileRole
        from files_catalog import seg_mask

        lesion_seg = RecordTypeDef(
            name="lesion_seg",
            description="Lesion segmentation",
            level="SERIES",
            files=[FileRef(seg_mask, role=FileRole.INPUT)],
        )
        """,
    )

    config_items = await load_python_config(tmp_path)
    assert len(config_items) == 1
    assert config_items[0].name == "lesion_seg"

    result = await reconcile_record_types(config_items, test_session)
    assert "lesion_seg" in result.created

    # Verify in DB
    stmt = select(RecordType).where(RecordType.name == "lesion_seg")
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
        from src.config.primitives import File

        master_model = File(pattern="master.nrrd", description="Master model")
        """,
    )
    _write_record_types(
        tmp_path,
        """\
        from src.config.primitives import RecordTypeDef, FileRef
        from src.models.file_schema import FileRole
        from files_catalog import master_model

        ai_analysis = RecordTypeDef(
            name="ai_analysis",
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
        .where(RecordType.name == "ai_analysis")
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
        from src.config.primitives import RecordTypeDef

        sidecar_type = RecordTypeDef(
            name="sidecar_type",
            description="Type with sidecar schema",
            level="SERIES",
        )
        """,
    )
    # Write sidecar schema
    schema = {"type": "object", "properties": {"grade": {"type": "integer"}}}
    (tmp_path / "sidecar_type.schema.json").write_text(json.dumps(schema))

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
        from src.config.primitives import RecordTypeDef

        mutable_type = RecordTypeDef(
            name="mutable_type",
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
        from src.config.primitives import RecordTypeDef

        mutable_type = RecordTypeDef(
            name="mutable_type",
            description="Version 2",
            level="STUDY",
        )
        """,
    )
    (tmp_path / "mutable_type.schema.json").write_text('{"type": "object"}')

    config_items = await load_python_config(tmp_path)
    result = await reconcile_record_types(config_items, test_session)
    assert "mutable_type" in result.updated

    stmt = select(RecordType).where(RecordType.name == "mutable_type")
    rt = (await test_session.execute(stmt)).scalar_one()
    assert rt.description == "Version 2"
    assert rt.level == "STUDY"


@pytest.mark.asyncio
async def test_python_mode_no_record_types_file(tmp_path) -> None:
    """Missing record_types.py → empty list."""
    result = await load_python_config(tmp_path)
    assert result == []
