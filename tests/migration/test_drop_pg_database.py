"""Unit tests for the hardened drop_pg_database teardown helper (#446).

Mock-based — no real PostgreSQL. Verifies the retry / give-up / happy-path
behaviour of the scratch-DB drop used by the migration_project fixture.
"""

from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from tests.migration import conftest


def _op_error(msg: str = "statement timeout") -> OperationalError:
    return OperationalError("DROP DATABASE", {}, Exception(msg))


def _install_fake_engine(monkeypatch, execute):
    conn = MagicMock()
    conn.execute.side_effect = execute
    engine = MagicMock()
    cm = engine.connect.return_value
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False  # do not swallow exceptions raised in the with-block
    calls: list[dict] = []

    def fake_create_engine(*a, **k):
        calls.append(k)
        return engine

    monkeypatch.setattr(conftest, "create_engine", fake_create_engine)
    monkeypatch.setattr(conftest.time, "sleep", lambda *_: None)
    return engine, conn, calls


def test_happy_path_terminates_then_drops(monkeypatch):
    executed: list[str] = []

    def execute(stmt, *a, **k):
        executed.append(str(stmt))
        return MagicMock()

    engine, _, calls = _install_fake_engine(monkeypatch, execute)
    conftest.drop_pg_database("scratch_db", "postgresql+psycopg2://u@h:5432")

    assert any("pg_terminate_backend" in s for s in executed)
    assert sum("DROP DATABASE" in s for s in executed) == 1
    engine.dispose.assert_called_once()
    # the fix's core: the admin engine is time-bounded with a fresh connection per op
    assert calls[0]["poolclass"] is conftest.NullPool
    assert calls[0]["connect_args"]["connect_timeout"] == 8
    assert "statement_timeout=10000" in calls[0]["connect_args"]["options"]


def test_retries_then_succeeds(monkeypatch):
    drops = {"n": 0}

    def execute(stmt, *a, **k):
        if "DROP DATABASE" in str(stmt):
            drops["n"] += 1
            if drops["n"] == 1:
                raise _op_error()
        return MagicMock()

    _install_fake_engine(monkeypatch, execute)
    conftest.drop_pg_database("scratch_db", "postgresql+psycopg2://u@h:5432")

    assert drops["n"] == 2  # failed once, succeeded on the retry


def test_gives_up_without_raising(monkeypatch):
    drops = {"n": 0}

    def execute(stmt, *a, **k):
        if "DROP DATABASE" in str(stmt):
            drops["n"] += 1
            raise _op_error()
        return MagicMock()

    _install_fake_engine(monkeypatch, execute)
    fake_logger = MagicMock()
    monkeypatch.setattr(conftest, "logger", fake_logger)

    conftest.drop_pg_database("scratch_db", "postgresql+psycopg2://u@h:5432")  # must not raise

    assert drops["n"] == conftest._DROP_ATTEMPTS
    fake_logger.warning.assert_called_once()
