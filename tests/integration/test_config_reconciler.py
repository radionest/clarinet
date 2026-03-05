"""Integration tests for the config reconciler.

Uses real DB sessions and RecordType objects — no mocks.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.config.reconciler import reconcile_record_types
from src.models.record import RecordType, RecordTypeCreate


def _make_config(
    name: str,
    description: str = "test",
    level: str = "SERIES",
    **extra: object,
) -> RecordTypeCreate:
    """Helper to create a minimal valid RecordTypeCreate."""
    return RecordTypeCreate(name=name, description=description, level=level, **extra)


@pytest_asyncio.fixture
async def seed_record_type(test_session: AsyncSession) -> RecordType:
    """Insert a RecordType into the test DB."""
    rt = RecordType(
        name="existing_type",
        description="Original description",
        level="SERIES",
        label="Original",
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest.mark.asyncio
async def test_create_new_record_types(test_session: AsyncSession) -> None:
    """Empty DB + config → all created."""
    config = [
        _make_config("alpha_test"),
        _make_config("bravo_test"),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.created == ["alpha_test", "bravo_test"]
    assert result.updated == []
    assert result.unchanged == []
    assert result.orphaned == []
    assert result.errors == []

    # Verify in DB
    stmt = select(RecordType).where(RecordType.name == "alpha_test")
    row = (await test_session.execute(stmt)).scalar_one()
    assert row.description == "test"


@pytest.mark.asyncio
async def test_update_changed_fields(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Existing RT + changed config → updated."""
    config = [
        _make_config(
            "existing_type",
            description="Updated description",
            label="Updated",
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing_type"]
    assert result.created == []

    await test_session.refresh(seed_record_type)
    assert seed_record_type.description == "Updated description"
    assert seed_record_type.label == "Updated"


@pytest.mark.asyncio
async def test_unchanged_not_modified(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Matching RT → unchanged."""
    config = [
        _make_config(
            "existing_type",
            description="Original description",
            label="Original",
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.unchanged == ["existing_type"]
    assert result.updated == []


@pytest.mark.asyncio
async def test_orphan_detection(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """RT in DB not in config → orphaned list."""
    config = [_make_config("new_config_type")]
    result = await reconcile_record_types(config, test_session, delete_orphans=False)

    assert "existing_type" in result.orphaned
    assert "new_config_type" in result.created

    # Orphan should still exist in DB
    stmt = select(RecordType).where(RecordType.name == "existing_type")
    row = (await test_session.execute(stmt)).scalar_one_or_none()
    assert row is not None


@pytest.mark.asyncio
async def test_orphan_deletion(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """delete_orphans=True → deleted."""
    config = [_make_config("new_config_type")]
    result = await reconcile_record_types(config, test_session, delete_orphans=True)

    assert "existing_type" in result.orphaned

    # Orphan should be deleted from DB
    stmt = select(RecordType).where(RecordType.name == "existing_type")
    row = (await test_session.execute(stmt)).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_file_registry_diff(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Changed file definitions detected."""
    file_def = {
        "name": "output_seg",
        "pattern": "seg.nrrd",
        "role": "output",
        "required": True,
        "multiple": False,
    }
    config = [
        _make_config(
            "existing_type",
            description="Original description",
            label="Original",
            file_registry=[file_def],
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing_type"]


@pytest.mark.asyncio
async def test_data_schema_diff(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Changed schema detected."""
    schema = {"type": "object", "properties": {"score": {"type": "number"}}}
    config = [
        _make_config(
            "existing_type",
            description="Original description",
            label="Original",
            data_schema=schema,
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing_type"]


@pytest.mark.asyncio
async def test_reconcile_result_counts(test_session: AsyncSession) -> None:
    """Mixed create/update/unchanged."""
    # Seed two types
    for name in ("type_alpha1", "type_bravo1"):
        rt = RecordType(name=name, description="original", level="SERIES")
        test_session.add(rt)
    await test_session.commit()

    config = [
        _make_config("type_alpha1", description="original"),  # unchanged
        _make_config("type_bravo1", description="modified"),  # updated
        _make_config("type_delta1"),  # created
    ]
    result = await reconcile_record_types(config, test_session)

    assert len(result.unchanged) == 1
    assert len(result.updated) == 1
    assert len(result.created) == 1
    assert result.errors == []


@pytest.mark.asyncio
async def test_none_config_matches_orm_default(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Config with min_users=None should match DB ORM default (1) → unchanged."""
    config = [
        _make_config(
            "existing_type",
            description="Original description",
            label="Original",
            min_users=None,
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.unchanged == ["existing_type"]
    assert result.updated == []


@pytest.mark.asyncio
async def test_explicit_value_differs_from_default(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Config with explicit min_users=3 should detect update vs DB default."""
    config = [
        _make_config(
            "existing_type",
            description="Original description",
            label="Original",
            min_users=3,
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing_type"]
    assert "min_users" in result.updated or result.updated == ["existing_type"]

    await test_session.refresh(seed_record_type)
    assert seed_record_type.min_users == 3


@pytest.mark.asyncio
async def test_empty_collection_matches_factory_default(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Config with data_schema={} and file_registry=[] should match ORM defaults → unchanged."""
    config = [
        _make_config(
            "existing_type",
            description="Original description",
            label="Original",
            data_schema={},
            file_registry=[],
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.unchanged == ["existing_type"]
    assert result.updated == []
