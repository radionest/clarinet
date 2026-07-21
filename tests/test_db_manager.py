"""Tests for SQLite foreign-key enforcement in ``DatabaseManager``.

Covers:
- ``PRAGMA foreign_keys=ON`` is set per-connection on the app's real engine.
  ``tests/conftest.py``'s ``test_engine`` fixture sets its own FK pragma
  independently — these tests build a fresh ``DatabaseManager`` to actually
  exercise the app's own connect-hook (``_set_sqlite_pragmas``).
- DB-level ``ON DELETE CASCADE`` (Task 3, ``Record.parent_record_id``) fires
  on SQLite for a raw SQL DELETE issued through the app engine — parity with
  PostgreSQL, which enforces FKs natively regardless of any pragma.
- Startup audit (``PRAGMA foreign_key_check``) logs a WARNING per dangling
  row and never aborts.
"""

import sqlite3

import pytest
import pytest_asyncio
from sqlalchemy import text

from clarinet.models.base import DicomQueryLevel
from clarinet.settings import settings
from clarinet.utils.db_manager import DatabaseManager
from tests.utils.factories import make_patient, make_record_type, seed_record


@pytest_asyncio.fixture
async def app_db_manager(tmp_path, monkeypatch):
    """A fresh ``DatabaseManager`` pointed at a real, file-based SQLite DB.

    A dedicated instance (not the module-level ``db_manager`` singleton) so
    tests never bleed state into each other under xdist. File-based (not
    ``:memory:``) because ``_set_sqlite_pragmas`` only registers its
    connect-hook for file-backed SQLite.
    """
    db_stem = tmp_path / "db_manager_fk_test"
    db_path = tmp_path / "db_manager_fk_test.db"
    monkeypatch.setattr(settings, "database_name", str(db_stem))
    monkeypatch.setattr(settings, "database_driver", "sqlite")
    monkeypatch.setattr(settings, "debug", False)

    dm = DatabaseManager()
    yield dm, db_path
    await dm.close()


@pytest.mark.asyncio
async def test_app_engine_connection_reports_foreign_keys_on(app_db_manager):
    """A connection from DatabaseManager's engine reports ``foreign_keys`` == 1."""
    dm, _ = app_db_manager

    async with dm.async_engine.connect() as conn:
        result = await conn.execute(text("PRAGMA foreign_keys"))
        value = result.scalar()

    assert value == 1


@pytest.mark.asyncio
async def test_raw_delete_of_parent_cascades_to_children_on_sqlite(app_db_manager):
    """Raw SQL DELETE of a parent record cascades to children on SQLite.

    Parity with PostgreSQL, which enforces ``ondelete="CASCADE"`` natively.
    Exercises Task 3's ``Record.parent_record_id`` FK.
    """
    dm, _ = app_db_manager
    await dm.create_db_and_tables_async()

    async with dm.get_async_session_context() as session:
        session.add(make_patient("FKPAT"))
        await session.commit()

        session.add(make_record_type("fk-cascade-rt", level=DicomQueryLevel.PATIENT))
        await session.commit()

        parent = await seed_record(
            session, patient_id="FKPAT", study_uid=None, series_uid=None, rt_name="fk-cascade-rt"
        )
        child = await seed_record(
            session,
            patient_id="FKPAT",
            study_uid=None,
            series_uid=None,
            rt_name="fk-cascade-rt",
            parent_record_id=parent.id,
        )
        parent_id, child_id = parent.id, child.id

    async with dm.async_engine.begin() as conn:
        await conn.execute(text("DELETE FROM record WHERE id = :id"), {"id": parent_id})

    async with dm.async_engine.connect() as conn:
        result = await conn.execute(text("SELECT id FROM record WHERE id = :id"), {"id": child_id})
        assert result.first() is None


@pytest.mark.asyncio
async def test_startup_audit_warns_on_dangling_row_and_completes(app_db_manager, caplog):
    """A pre-existing dangling row logs one WARNING per violation; startup completes.

    Simulates a legacy DB: the dangling row is created via a raw stdlib
    ``sqlite3`` connection (FK enforcement OFF by default), bypassing the app
    engine's connect-hook and its ``ON DELETE CASCADE`` action entirely —
    exactly how a pre-Task-9 database could end up with an orphaned row.
    """
    dm, db_path = app_db_manager
    await dm.create_db_and_tables_async()

    async with dm.get_async_session_context() as session:
        session.add(make_patient("FKPAT2"))
        await session.commit()

        session.add(make_record_type("fk-audit-rt", level=DicomQueryLevel.PATIENT))
        await session.commit()

        await seed_record(
            session, patient_id="FKPAT2", study_uid=None, series_uid=None, rt_name="fk-audit-rt"
        )

    raw = sqlite3.connect(db_path)
    raw.execute("DELETE FROM patient WHERE id = ?", ("FKPAT2",))
    raw.commit()
    raw.close()

    with caplog.at_level("WARNING"):
        await dm.create_db_and_tables_async()  # must not raise

    messages = " ".join(r.message for r in caplog.records)
    assert "table=record" in messages
    assert "parent=patient" in messages


@pytest.mark.asyncio
async def test_startup_audit_silent_when_no_violations(app_db_manager, caplog):
    """Zero foreign-key violations → no warnings logged."""
    dm, _ = app_db_manager

    with caplog.at_level("WARNING"):
        await dm.create_db_and_tables_async()

    assert caplog.records == []
