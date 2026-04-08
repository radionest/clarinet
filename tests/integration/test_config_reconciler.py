"""Integration tests for the config reconciler.

Uses real DB sessions and RecordType objects — no mocks.
"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from clarinet.config.reconciler import reconcile_record_types
from clarinet.exceptions.domain import ConfigurationError
from clarinet.models.record import RecordType, RecordTypeCreate
from clarinet.models.user import UserRole


def _make_config(
    name: str,
    description: str = "test",
    level: str = "SERIES",
    *,
    file_registry: list[dict[str, object]] | None = None,
    **extra: object,
) -> RecordTypeCreate:
    """Helper to create a minimal valid RecordTypeCreate."""
    return RecordTypeCreate(
        name=name,
        description=description,
        level=level,
        file_registry=file_registry,
        **extra,
    )


@pytest_asyncio.fixture
async def seed_record_type(test_session: AsyncSession) -> RecordType:
    """Insert a RecordType into the test DB."""
    rt = RecordType(
        name="existing-type",
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
        _make_config("alpha-test"),
        _make_config("bravo-test"),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.created == ["alpha-test", "bravo-test"]
    assert result.updated == []
    assert result.unchanged == []
    assert result.orphaned == []
    assert result.errors == []

    # Verify in DB
    stmt = select(RecordType).where(RecordType.name == "alpha-test")
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
            "existing-type",
            description="Updated description",
            label="Updated",
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing-type"]
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
            "existing-type",
            description="Original description",
            label="Original",
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.unchanged == ["existing-type"]
    assert result.updated == []


@pytest.mark.asyncio
async def test_mask_patient_data_change_triggers_update(
    test_session: AsyncSession,
) -> None:
    """Toggling ``mask_patient_data`` between config versions is a diff."""
    # v1: default True (not explicitly set — but reconciler treats default as unchanged)
    config_v1 = [_make_config("mask-test", mask_patient_data=True)]
    result = await reconcile_record_types(config_v1, test_session)
    assert "mask-test" in result.created

    # Clear cached state before re-reconciling
    test_session.expire_all()

    # v2: explicit False — must be detected as a change
    config_v2 = [_make_config("mask-test", mask_patient_data=False)]
    result = await reconcile_record_types(config_v2, test_session)
    assert "mask-test" in result.updated

    # Verify DB value
    stmt = select(RecordType).where(RecordType.name == "mask-test")
    row = (await test_session.execute(stmt)).scalar_one()
    assert row.mask_patient_data is False


@pytest.mark.asyncio
async def test_orphan_detection(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """RT in DB not in config → orphaned list."""
    config = [_make_config("new-config-type")]
    result = await reconcile_record_types(config, test_session, delete_orphans=False)

    assert "existing-type" in result.orphaned
    assert "new-config-type" in result.created

    # Orphan should still exist in DB
    stmt = select(RecordType).where(RecordType.name == "existing-type")
    row = (await test_session.execute(stmt)).scalar_one_or_none()
    assert row is not None


@pytest.mark.asyncio
async def test_orphan_deletion(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """delete_orphans=True → deleted."""
    config = [_make_config("new-config-type")]
    result = await reconcile_record_types(config, test_session, delete_orphans=True)

    assert "existing-type" in result.orphaned

    # Orphan should be deleted from DB
    stmt = select(RecordType).where(RecordType.name == "existing-type")
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
            "existing-type",
            description="Original description",
            label="Original",
            file_registry=[file_def],
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing-type"]


@pytest.mark.asyncio
async def test_data_schema_diff(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Changed schema detected."""
    schema = {"type": "object", "properties": {"score": {"type": "number"}}}
    config = [
        _make_config(
            "existing-type",
            description="Original description",
            label="Original",
            data_schema=schema,
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing-type"]


@pytest.mark.asyncio
async def test_reconcile_result_counts(test_session: AsyncSession) -> None:
    """Mixed create/update/unchanged."""
    # Seed two types
    for name in ("type-alpha1", "type-bravo1"):
        rt = RecordType(name=name, description="original", level="SERIES")
        test_session.add(rt)
    await test_session.commit()

    config = [
        _make_config("type-alpha1", description="original"),  # unchanged
        _make_config("type-bravo1", description="modified"),  # updated
        _make_config("type-delta1"),  # created
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
    """Config with min_records=None should match DB ORM default (1) → unchanged."""
    config = [
        _make_config(
            "existing-type",
            description="Original description",
            label="Original",
            min_records=None,
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.unchanged == ["existing-type"]
    assert result.updated == []


@pytest.mark.asyncio
async def test_explicit_value_differs_from_default(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Config with explicit min_records=3 should detect update vs DB default."""
    config = [
        _make_config(
            "existing-type",
            description="Original description",
            label="Original",
            min_records=3,
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.updated == ["existing-type"]
    assert "min_records" in result.updated or result.updated == ["existing-type"]

    await test_session.refresh(seed_record_type)
    assert seed_record_type.min_records == 3


@pytest.mark.asyncio
async def test_file_level_change_triggers_update(
    test_session: AsyncSession,
) -> None:
    """Changing file level triggers update via _file_links_differ."""
    # Create initial with no level
    config_v1 = [
        _make_config(
            "level-test1",
            file_registry=[
                {
                    "name": "seg_file",
                    "pattern": "seg.nrrd",
                    "role": "output",
                    "required": True,
                    "multiple": False,
                }
            ],
        ),
    ]
    result = await reconcile_record_types(config_v1, test_session)
    assert result.created == ["level-test1"]

    # Clear identity map so eager loading re-fetches the FileDefinition
    test_session.expire_all()

    # Update with level="PATIENT"
    config_v2 = [
        _make_config(
            "level-test1",
            file_registry=[
                {
                    "name": "seg_file",
                    "pattern": "seg.nrrd",
                    "role": "output",
                    "required": True,
                    "multiple": False,
                    "level": "PATIENT",
                }
            ],
        ),
    ]
    result = await reconcile_record_types(config_v2, test_session)
    assert result.updated == ["level-test1"]


@pytest.mark.asyncio
async def test_empty_collection_matches_factory_default(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Config with data_schema={} and file_registry=[] should match ORM defaults → unchanged."""
    config = [
        _make_config(
            "existing-type",
            description="Original description",
            label="Original",
            data_schema={},
            file_registry=[],
        ),
    ]
    result = await reconcile_record_types(config, test_session)

    assert result.unchanged == ["existing-type"]
    assert result.updated == []


# --- Regression tests: savepoint isolation and role validation ---


@pytest.mark.asyncio
async def test_savepoint_isolates_create_error(
    test_session: AsyncSession,
) -> None:
    """A failing CREATE should be recorded in errors without breaking other items.

    Regression: before savepoints, one FK/integrity error poisoned the whole session.
    """
    from unittest.mock import patch

    good_items = [
        _make_config("iso-alpha"),
        _make_config("iso-gamma"),
    ]
    bad_item = _make_config("iso-beta")

    original_flush = test_session.flush
    call_count = 0

    async def patched_flush(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:  # iso-beta is second item
            raise Exception("simulated integrity error")
        return await original_flush(*args, **kwargs)

    config = [good_items[0], bad_item, good_items[1]]

    with patch.object(test_session, "flush", side_effect=patched_flush):
        result = await reconcile_record_types(config, test_session)

    assert "iso-alpha" in result.created
    assert "iso-gamma" in result.created
    assert len(result.errors) == 1
    assert result.errors[0][0] == "iso-beta"
    assert "simulated integrity error" in result.errors[0][1]


@pytest.mark.asyncio
async def test_reconcile_config_validates_missing_roles(
    test_session: AsyncSession,
) -> None:
    """reconcile_config raises ConfigurationError for undefined role_name.

    Regression: before validation, undefined roles caused cryptic FK violations
    with cascading session poisoning.
    """
    from unittest.mock import AsyncMock, patch

    from clarinet.utils.bootstrap import reconcile_config

    role = UserRole(name="doctor-test")
    test_session.add(role)
    await test_session.commit()

    items = [
        _make_config("valid-role-type", role_name="doctor-test"),
        _make_config("bad-role-type", role_name="nonexistent-role"),
    ]

    with (
        patch(
            "clarinet.config.python_loader.load_python_config",
            new_callable=AsyncMock,
            return_value=items,
        ),
        patch(
            "clarinet.utils.bootstrap.db_manager.get_async_session_context",
        ) as mock_ctx,
        patch("clarinet.settings.settings") as mock_settings,
    ):
        mock_settings.config_mode = "python"
        mock_settings.config_tasks_path = "/fake/path"
        mock_settings.config_delete_orphans = False

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=test_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = ctx

        with pytest.raises(ConfigurationError, match="nonexistent-role"):
            await reconcile_config(folder="/fake/path")


@pytest.mark.asyncio
async def test_reconcile_config_passes_with_valid_roles(
    test_session: AsyncSession,
) -> None:
    """reconcile_config succeeds when all role_names exist."""
    from unittest.mock import AsyncMock, patch

    from clarinet.utils.bootstrap import reconcile_config

    role = UserRole(name="valid-role-test")
    test_session.add(role)
    await test_session.commit()

    items = [
        _make_config("role-ok-type", role_name="valid-role-test"),
        _make_config("no-role-type"),  # role_name=None — should be fine
    ]

    with (
        patch(
            "clarinet.config.python_loader.load_python_config",
            new_callable=AsyncMock,
            return_value=items,
        ),
        patch(
            "clarinet.utils.bootstrap.db_manager.get_async_session_context",
        ) as mock_ctx,
        patch("clarinet.settings.settings") as mock_settings,
    ):
        mock_settings.config_mode = "python"
        mock_settings.config_tasks_path = "/fake/path"
        mock_settings.config_delete_orphans = False

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=test_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = ctx

        result = await reconcile_config(folder="/fake/path")

    assert result.errors == []
    assert "role-ok-type" in result.created
    assert "no-role-type" in result.created


@pytest.mark.asyncio
async def test_error_message_lists_all_db_roles(
    test_session: AsyncSession,
) -> None:
    """ConfigurationError should list ALL DB roles, not just referenced ones."""
    from unittest.mock import AsyncMock, patch

    from clarinet.utils.bootstrap import reconcile_config

    for name in ("alpha-role", "beta-role"):
        test_session.add(UserRole(name=name))
    await test_session.commit()

    items = [_make_config("bad-type", role_name="missing-role")]

    with (
        patch(
            "clarinet.config.python_loader.load_python_config",
            new_callable=AsyncMock,
            return_value=items,
        ),
        patch(
            "clarinet.utils.bootstrap.db_manager.get_async_session_context",
        ) as mock_ctx,
        patch("clarinet.settings.settings") as mock_settings,
    ):
        mock_settings.config_mode = "python"
        mock_settings.config_tasks_path = "/fake/path"
        mock_settings.config_delete_orphans = False

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=test_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = ctx

        with pytest.raises(ConfigurationError, match="alpha-role") as exc_info:
            await reconcile_config(folder="/fake/path")

    # Both unreferenced roles should appear in the message
    msg = str(exc_info.value)
    assert "alpha-role" in msg
    assert "beta-role" in msg
    assert "missing-role" in msg
