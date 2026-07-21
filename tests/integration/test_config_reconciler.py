"""Integration tests for the config reconciler.

Uses real DB sessions and RecordType objects — no mocks.
"""

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from clarinet.config.reconciler import _COMPARED_FIELDS, ReconcileResult, reconcile_record_types
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
    # v1: explicit True — matches the ORM default, so creation only
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
async def test_inherit_user_from_parent_change_triggers_update(
    test_session: AsyncSession,
) -> None:
    """Toggling ``inherit_user_from_parent`` between config versions is a diff."""
    config_v1 = [_make_config("inherit-test", inherit_user_from_parent=False)]
    result = await reconcile_record_types(config_v1, test_session)
    assert "inherit-test" in result.created

    # Clear cached state before re-reconciling
    test_session.expire_all()

    config_v2 = [_make_config("inherit-test", inherit_user_from_parent=True)]
    result = await reconcile_record_types(config_v2, test_session)
    assert "inherit-test" in result.updated

    stmt = select(RecordType).where(RecordType.name == "inherit-test")
    row = (await test_session.execute(stmt)).scalar_one()
    assert row.inherit_user_from_parent is True


@pytest.mark.asyncio
async def test_editable_flags_change_triggers_update(
    test_session: AsyncSession,
) -> None:
    """Toggling ``editable`` / ``edit_window_days`` between config versions is a diff."""
    config_v1 = [_make_config("editable-test", editable=True)]
    result = await reconcile_record_types(config_v1, test_session)
    assert "editable-test" in result.created

    # Clear cached state before re-reconciling
    test_session.expire_all()

    config_v2 = [_make_config("editable-test", editable=False, edit_window_days=14)]
    result = await reconcile_record_types(config_v2, test_session)
    assert "editable-test" in result.updated

    stmt = select(RecordType).where(RecordType.name == "editable-test")
    row = (await test_session.execute(stmt)).scalar_one()
    assert row.editable is False
    assert row.edit_window_days == 14


@pytest.mark.asyncio
async def test_shared_editing_change_triggers_update(test_session: AsyncSession) -> None:
    """Toggling ``shared_editing`` between config versions is a diff.

    Both versions pin ``unique_per_user=False`` (the model invariant forbids
    ``shared_editing=True`` with ``unique_per_user=True``), so ``shared_editing``
    is the only field that differs between v1 and v2.
    """
    config_v1 = [_make_config("shared-diff-test", shared_editing=False, unique_per_user=False)]
    result = await reconcile_record_types(config_v1, test_session)
    assert "shared-diff-test" in result.created

    test_session.expire_all()  # drop cached attrs before the update pass

    config_v2 = [_make_config("shared-diff-test", shared_editing=True, unique_per_user=False)]
    result = await reconcile_record_types(config_v2, test_session)
    assert "shared-diff-test" in result.updated

    test_session.expire_all()
    stmt = select(RecordType).where(RecordType.name == "shared-diff-test")
    row = (await test_session.execute(stmt)).scalars().first()
    assert row.shared_editing is True


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
        "pattern": "seg_{id}.nrrd",  # {id} discriminates -> path-uniqueness check no-ops
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
async def test_unique_by_reorder_is_unchanged(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """A reordered but set-equal ``unique_by`` must not trigger an update.

    The DB row holds the raw JSON list ``["parent", "user"]`` (the default, as
    written by the round-trip through ``PortableJSON``); config sets the same
    partition as a set literal in the opposite insertion order. A naive
    list-vs-frozenset ``==`` would report this as changed on every reconcile —
    a list and a frozenset never compare equal in Python regardless of
    contents. Canonical comparison must treat them as equal.
    """
    assert seed_record_type.unique_by == ["parent", "user"]  # raw list, DB round-trip

    cfg = _make_config(
        "existing-type",
        description="Original description",
        label="Original",
        unique_by={"user", "parent"},
    )
    result = await reconcile_record_types([cfg], test_session)

    assert result.unchanged == ["existing-type"]
    assert result.updated == []


@pytest.mark.asyncio
async def test_unique_by_none_vs_set_is_a_change(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """DB ``None`` vs. a configured partition set is a real diff, not a no-op."""
    seed_record_type.unique_by = None
    test_session.add(seed_record_type)
    await test_session.commit()
    test_session.expire_all()

    cfg = _make_config(
        "existing-type",
        description="Original description",
        label="Original",
        unique_by={"user"},
    )
    result = await reconcile_record_types([cfg], test_session)

    assert result.updated == ["existing-type"]
    test_session.expire_all()
    row = (
        await test_session.execute(select(RecordType).where(RecordType.name == "existing-type"))
    ).scalar_one()
    assert row.unique_by == ["user"]


@pytest.mark.asyncio
async def test_unique_by_explicit_none_overwrites_default(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Explicit ``unique_by=None`` is a real target value ("off") — it must
    overwrite a DB row holding the default partition set instead of being
    skipped as "config left this unset" (contrast with
    ``test_none_config_matches_orm_default``, where an explicit
    ``min_records=None`` IS treated as a no-op against that field's default).
    """
    assert seed_record_type.unique_by == ["parent", "user"]  # DB holds the default

    cfg = _make_config(
        "existing-type",
        description="Original description",
        label="Original",
        unique_by=None,
    )
    assert "unique_by" in cfg.model_fields_set  # precondition: explicitly set

    result = await reconcile_record_types([cfg], test_session)

    assert result.updated == ["existing-type"]
    test_session.expire_all()
    row = (
        await test_session.execute(select(RecordType).where(RecordType.name == "existing-type"))
    ).scalar_one()
    assert row.unique_by is None


@pytest.mark.asyncio
async def test_unset_flag_heals_drifted_db_value(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """A DB row drifted from a non-None model default heals to that default even
    when the config leaves the field unset. Regression for issue #389: a
    migration-backfilled ``unique_by=None`` must reconcile to the documented
    default ``{"user", "parent"}`` on restart, not survive forever.
    """
    # Drift the DB row away from the model default ({"user", "parent"}), as a
    # migration backfill would (direct assignment skips validation, mirroring
    # a raw DB row).
    seed_record_type.unique_by = None
    test_session.add(seed_record_type)
    await test_session.commit()
    test_session.expire_all()

    cfg = _make_config("existing-type", description="Original description", label="Original")
    assert "unique_by" not in cfg.model_fields_set  # precondition: field unset

    result = await reconcile_record_types([cfg], test_session)

    assert result.updated == ["existing-type"]
    test_session.expire_all()
    row = (
        await test_session.execute(select(RecordType).where(RecordType.name == "existing-type"))
    ).scalar_one()
    assert row.unique_by == ["parent", "user"]


@pytest.mark.asyncio
async def test_unset_heal_is_asymmetric_by_default_kind(
    test_session: AsyncSession,
    seed_record_type: RecordType,
) -> None:
    """Unset-field healing keys off the *kind* of default (issue #389).

    ``min_records`` has a concrete default (1), so a drifted DB value heals to it
    even when the config leaves it unset; ``max_records`` defaults to ``None``, so
    an unset config keeps the "leave the DB row untouched" contract. The two
    adjacent int fields deliberately diverge — a hand-written TOML that omits
    ``min_records`` silently collapses a drifted DB value back to 1.
    """
    seed_record_type.min_records = 5  # drift from concrete default 1 → should heal
    seed_record_type.max_records = 7  # None-default field → must stay put
    test_session.add(seed_record_type)
    await test_session.commit()
    test_session.expire_all()

    cfg = _make_config("existing-type", description="Original description", label="Original")
    assert "min_records" not in cfg.model_fields_set
    assert "max_records" not in cfg.model_fields_set

    result = await reconcile_record_types([cfg], test_session)

    assert result.updated == ["existing-type"]
    test_session.expire_all()
    row = (
        await test_session.execute(select(RecordType).where(RecordType.name == "existing-type"))
    ).scalar_one()
    assert row.min_records == 1  # healed to concrete default
    assert row.max_records == 7  # None-default: DB value preserved


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
    # {id} discriminates regardless of unique_by/level -> path-uniqueness check
    # no-ops here; the diff being tested is (name, role, required, level) only.
    # Create initial with no level
    config_v1 = [
        _make_config(
            "level-test1",
            file_registry=[
                {
                    "name": "seg_file",
                    "pattern": "seg_{id}.nrrd",
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
                    "pattern": "seg_{id}.nrrd",
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


async def _reconcile_with_mocked_env(
    test_session: AsyncSession, items: list[RecordTypeCreate]
) -> "ReconcileResult":
    """Run ``reconcile_config`` over *items* with mocked python-mode settings.

    Shared by the registry-reference guard tests (data_validators /
    slicer_context_hydrators). ``spec_set=True`` keeps attribute names
    honest — a typo in a ``config_*_file`` assignment would raise instead
    of silently leaving the real attribute as an auto-created MagicMock
    (see tests/CLAUDE.md → MagicMock pitfalls).
    """
    from unittest.mock import AsyncMock, patch

    from clarinet.utils.bootstrap import reconcile_config

    with (
        patch(
            "clarinet.config.python_loader.load_python_config",
            new_callable=AsyncMock,
            return_value=items,
        ),
        patch(
            "clarinet.utils.bootstrap.db_manager.get_async_session_context",
        ) as mock_ctx,
        patch("clarinet.settings.settings", spec_set=True) as mock_settings,
    ):
        mock_settings.config_mode = "python"
        mock_settings.config_tasks_path = "/fake/path"
        mock_settings.config_validators_file = "validators.py"
        mock_settings.config_context_hydrators_file = "context_hydrators.py"
        mock_settings.config_schema_hydrators_file = "schema_hydrators.py"
        mock_settings.config_delete_orphans = False

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=test_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = ctx

        return await reconcile_config(folder="/fake/path")


@pytest.mark.asyncio
async def test_reconcile_config_validates_data_validators_registered(
    test_session: AsyncSession,
) -> None:
    """reconcile_config raises ConfigurationError when data_validators references
    an unregistered validator name.

    Mirrors the role-validation guard above — fail-fast at startup keeps a broken
    config from silently bypassing submit-time validation.
    """
    from clarinet.services.record_data_validation import _VALIDATOR_REGISTRY

    # Isolate registry so other tests' registrations don't bleed in.
    saved_registry = _VALIDATOR_REGISTRY.snapshot()
    _VALIDATOR_REGISTRY.clear()
    try:
        items = [
            _make_config(
                "rt-with-ghost-validator",
                data_validators=["plan.nonexistent_validator"],
            ),
        ]

        with pytest.raises(ConfigurationError, match=r"plan\.nonexistent_validator") as exc_info:
            await _reconcile_with_mocked_env(test_session, items)

        msg = str(exc_info.value)
        assert "rt-with-ghost-validator" in msg
        assert "validators.py" in msg
    finally:
        _VALIDATOR_REGISTRY.restore(saved_registry)


@pytest.mark.asyncio
async def test_reconcile_config_passes_with_registered_validator(
    test_session: AsyncSession,
) -> None:
    """reconcile_config succeeds when every data_validators name is registered."""
    from clarinet.services.record_data_validation import (
        _VALIDATOR_REGISTRY,
        record_validator,
    )

    saved_registry = _VALIDATOR_REGISTRY.snapshot()
    _VALIDATOR_REGISTRY.clear()
    try:

        @record_validator("test.registered_validator")
        async def _registered(record, data, ctx):
            return None

        items = [
            _make_config(
                "rt-with-registered-validator",
                data_validators=["test.registered_validator"],
            ),
            _make_config("rt-without-validators"),  # data_validators=None
        ]

        result = await _reconcile_with_mocked_env(test_session, items)

        assert result.errors == []
        assert "rt-with-registered-validator" in result.created
        assert "rt-without-validators" in result.created
    finally:
        _VALIDATOR_REGISTRY.restore(saved_registry)


@pytest.mark.asyncio
async def test_reconcile_config_validates_slicer_hydrators_registered(
    test_session: AsyncSession,
) -> None:
    """reconcile_config raises ConfigurationError when slicer_context_hydrators
    references an unregistered hydrator name.

    Mirrors the data_validators guard above — a typo here used to surface only
    at runtime, when the doctor opened the record in Slicer.
    """
    from clarinet.services.slicer.context_hydration import _SLICER_HYDRATOR_REGISTRY

    # Isolate registry so other tests' registrations don't bleed in.
    saved_registry = _SLICER_HYDRATOR_REGISTRY.snapshot()
    _SLICER_HYDRATOR_REGISTRY.clear()
    try:
        items = [
            _make_config(
                "rt-with-ghost-hydrator",
                slicer_context_hydrators=["plan.nonexistent_hydrator"],
            ),
        ]

        with pytest.raises(ConfigurationError, match=r"plan\.nonexistent_hydrator") as exc_info:
            await _reconcile_with_mocked_env(test_session, items)

        msg = str(exc_info.value)
        assert "rt-with-ghost-hydrator" in msg
        assert "context_hydrators.py" in msg
    finally:
        _SLICER_HYDRATOR_REGISTRY.restore(saved_registry)


@pytest.mark.asyncio
async def test_reconcile_config_passes_with_registered_slicer_hydrator(
    test_session: AsyncSession,
) -> None:
    """reconcile_config succeeds when every slicer_context_hydrators name is registered."""
    from clarinet.services.slicer.context_hydration import (
        _SLICER_HYDRATOR_REGISTRY,
        slicer_context_hydrator,
    )

    saved_registry = _SLICER_HYDRATOR_REGISTRY.snapshot()
    _SLICER_HYDRATOR_REGISTRY.clear()
    try:

        @slicer_context_hydrator("test.registered_hydrator")
        async def _registered(record, context, ctx):
            return {}

        items = [
            _make_config(
                "rt-with-registered-hydrator",
                slicer_context_hydrators=["test.registered_hydrator"],
            ),
            _make_config("rt-without-hydrators"),  # slicer_context_hydrators=None
        ]

        result = await _reconcile_with_mocked_env(test_session, items)

        assert result.errors == []
        assert "rt-with-registered-hydrator" in result.created
        assert "rt-without-hydrators" in result.created
    finally:
        _SLICER_HYDRATOR_REGISTRY.restore(saved_registry)


@pytest.mark.asyncio
async def test_reconcile_config_validates_schema_hydrators_registered(
    test_session: AsyncSession,
) -> None:
    """reconcile_config raises ConfigurationError when a data_schema x-options.source
    references an unregistered schema hydrator name.

    Mirrors the slicer-hydrator guard — a typo here used to surface only at render
    time as an "Unknown x-options source" warning, leaving the field raw.
    """
    from clarinet.services.schema_hydration import _HYDRATOR_REGISTRY

    saved_registry = _HYDRATOR_REGISTRY.snapshot()
    _HYDRATOR_REGISTRY.clear()
    try:
        items = [
            _make_config(
                "rt-with-ghost-source",
                data_schema={
                    "type": "object",
                    "properties": {
                        "series": {
                            "type": "string",
                            "x-options": {"source": "nonexistent_source"},
                        },
                    },
                },
            ),
        ]

        with pytest.raises(ConfigurationError, match=r"nonexistent_source") as exc_info:
            await _reconcile_with_mocked_env(test_session, items)

        msg = str(exc_info.value)
        assert "rt-with-ghost-source" in msg
        assert "schema_hydrators.py" in msg
    finally:
        _HYDRATOR_REGISTRY.restore(saved_registry)


@pytest.mark.asyncio
async def test_reconcile_config_passes_with_registered_schema_hydrator(
    test_session: AsyncSession,
) -> None:
    """reconcile_config succeeds when every x-options.source is registered."""
    from clarinet.services.schema_hydration import _HYDRATOR_REGISTRY, schema_hydrator

    saved_registry = _HYDRATOR_REGISTRY.snapshot()
    _HYDRATOR_REGISTRY.clear()
    try:

        @schema_hydrator("test.registered_source")
        async def _registered(record, options, ctx):
            return []

        items = [
            _make_config(
                "rt-with-registered-source",
                data_schema={
                    "type": "object",
                    "properties": {
                        "series": {
                            "type": "string",
                            "x-options": {"source": "test.registered_source"},
                        },
                    },
                },
            ),
            _make_config("rt-without-schema"),  # data_schema=None → no sources
        ]

        result = await _reconcile_with_mocked_env(test_session, items)

        assert result.errors == []
        assert "rt-with-registered-source" in result.created
        assert "rt-without-schema" in result.created
    finally:
        _HYDRATOR_REGISTRY.restore(saved_registry)


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


@pytest.mark.asyncio
async def test_reconcile_config_validates_allowed_viewers_configured(
    test_session: AsyncSession,
) -> None:
    """reconcile_config raises ConfigurationError when allowed_viewers references
    a viewer name absent from ``settings.viewers``.

    A typo (e.g. ["ohiff"]) would otherwise pass a non-empty allowlist matching
    no configured viewer, silently hiding every viewer button on the record
    page. Fail-fast at startup surfaces the misconfiguration instead.
    """
    from unittest.mock import AsyncMock, patch

    from clarinet.utils.bootstrap import reconcile_config

    items = [_make_config("rt-bad-viewer", allowed_viewers=["ohiff"])]

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
        mock_settings.viewers = {"ohif": {}, "radiant": {}}

        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=test_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = ctx

        with pytest.raises(ConfigurationError, match="ohiff") as exc_info:
            await reconcile_config(folder="/fake/path")

    msg = str(exc_info.value)
    assert "rt-bad-viewer" in msg
    assert "radiant" in msg  # configured viewers listed in the hint


def test_reconciler_compares_all_record_type_fields() -> None:
    """Drift guard: every RecordTypeCreate field is compared or excluded here.

    A field missing from _COMPARED_FIELDS is silently ignored on the update
    path for existing RecordTypes — unique_per_user hit this before being
    added.
    """
    intentionally_excluded = {
        "name",  # identity key — used to match, never updated
        "file_registry",  # compared separately via link diff
    }
    model_fields = set(RecordTypeCreate.model_fields)
    assert model_fields - set(_COMPARED_FIELDS) - intentionally_excluded == set()


def test_recordtypecreate_rejects_shared_editing_with_unique_by() -> None:
    """The ``shared_editing`` / ``unique_by`` invariant is enforced on the
    model (``RecordTypeBase``), so every write path that builds a
    ``RecordTypeCreate`` rejects the combo: config load AND the API
    (``POST /types`` deserializes the body into ``RecordTypeCreate`` → 422),
    not only the bootstrap config-load path.
    """
    with pytest.raises(ValidationError, match="unique_by"):
        _make_config("shared-bad", shared_editing=True, unique_by={"user"})


@pytest.mark.asyncio
async def test_toml_config_load_fails_fast_on_shared_editing_invariant(
    test_session: AsyncSession,
) -> None:
    """TOML mode must abort startup on the shared_editing/unique_per_user combo.

    The per-file loop builds ``RecordTypeCreate(**props)`` inside a ``try`` whose
    lenient ``except Exception`` would otherwise log-and-skip the model's
    ValidationError, silently dropping the type (later references 404 at
    runtime). The loop must convert it into a fatal ``ConfigurationError``.
    """
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from clarinet.utils.bootstrap import reconcile_config

    bad_props = {
        "name": "shared-bad",
        "level": "SERIES",
        "shared_editing": True,
        "unique_per_user": True,
    }

    with (
        patch(
            "clarinet.utils.bootstrap.discover_config_files",
            return_value=[Path("shared-bad.toml")],
        ),
        patch(
            "clarinet.utils.bootstrap.load_project_file_registry",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "clarinet.utils.bootstrap.load_record_config",
            new_callable=AsyncMock,
            return_value=bad_props,
        ),
        patch(
            "clarinet.utils.bootstrap.resolve_task_files",
            side_effect=lambda props, _reg: props,
        ),
        patch("clarinet.settings.settings") as mock_settings,
    ):
        mock_settings.config_mode = "toml"
        mock_settings.config_tasks_path = "/fake/path"
        mock_settings.config_delete_orphans = False

        with pytest.raises(ConfigurationError, match="Invalid record type config"):
            await reconcile_config(folder="/fake/path")


@pytest.mark.asyncio
async def test_toml_config_load_fails_fast_on_output_path_uniqueness(
    test_session: AsyncSession,
) -> None:
    """TOML mode must abort startup on an OUTPUT pattern that cannot
    discriminate coexisting records.

    Mirrors the shared_editing guard above: ``RecordTypeCreate(**props)``
    inside the per-file loop runs ``validate_output_path_uniqueness`` (via the
    model's ``_validate_output_paths`` model_validator), which raises
    ``RecordConstraintViolationError`` for a default ``unique_by`` (the
    "user" partition) paired with an OUTPUT pattern missing ``{user_id}``.
    The loop must convert that into a fatal ``ConfigLoadError`` naming the
    record type and the config file — not the lenient ``except Exception``
    log-and-skip, which would silently reconcile a type whose OUTPUT file
    collides across users. Carried over from task 6's review: this branch
    had no test.
    """
    from pathlib import Path
    from unittest.mock import AsyncMock, patch

    from clarinet.exceptions.domain import ConfigLoadError
    from clarinet.utils.bootstrap import reconcile_config

    bad_props = {
        "name": "bad-output-path",
        "level": "SERIES",
        "file_registry": [
            {
                "name": "result_file",
                "pattern": "result.txt",
                "role": "output",
                "required": True,
                "multiple": False,
            }
        ],
    }

    with (
        patch(
            "clarinet.utils.bootstrap.discover_config_files",
            return_value=[Path("bad-output-path.toml")],
        ),
        patch(
            "clarinet.utils.bootstrap.load_project_file_registry",
            new_callable=AsyncMock,
            return_value={},
        ),
        patch(
            "clarinet.utils.bootstrap.load_record_config",
            new_callable=AsyncMock,
            return_value=bad_props,
        ),
        patch(
            "clarinet.utils.bootstrap.resolve_task_files",
            side_effect=lambda props, _reg: props,
        ),
        patch("clarinet.settings.settings") as mock_settings,
    ):
        mock_settings.config_mode = "toml"
        mock_settings.config_tasks_path = "/fake/path"
        mock_settings.config_delete_orphans = False

        with pytest.raises(ConfigLoadError, match="bad-output-path") as exc_info:
            await reconcile_config(folder="/fake/path")

    assert exc_info.value.path == "bad-output-path.toml"
    assert exc_info.value.kind == "record type config"


@pytest.mark.asyncio
async def test_reconcile_config_allows_shared_editing_without_unique_per_user(
    test_session: AsyncSession,
) -> None:
    """shared_editing=True + unique_per_user=False reconciles cleanly."""
    from unittest.mock import AsyncMock, patch

    from clarinet.utils.bootstrap import reconcile_config

    items = [_make_config("shared-ok", shared_editing=True, unique_per_user=False)]

    with (
        patch(
            "clarinet.config.python_loader.load_python_config",
            new_callable=AsyncMock,
            return_value=items,
        ),
        patch("clarinet.utils.bootstrap.db_manager.get_async_session_context") as mock_ctx,
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
        assert "shared-ok" in result.created
