"""Unit tests for the ReportRepository SQL safety validator."""

import os

import pytest

from clarinet.exceptions.domain import ReportQueryError
from clarinet.repositories.report_repository import _validate_select_only


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1",
        "select 1",
        "  SELECT * FROM record",
        "WITH cte AS (SELECT 1) SELECT * FROM cte",
        "with recursive r AS (SELECT 1) SELECT * FROM r",
        "-- title: x\n-- description: y\nSELECT 1",
        "/* multi\nline */ SELECT 1",
        "/* a */ -- b\nSELECT 1",
    ],
)
def test_validate_accepts_select_and_with(sql: str) -> None:
    _validate_select_only(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "DROP TABLE record",
        "INSERT INTO record (id) VALUES (1)",
        "UPDATE record SET status = 'x'",
        "TRUNCATE record",
        "ALTER TABLE record ADD COLUMN x INT",
        "GRANT ALL ON record TO public",
        "-- title: malicious\nDELETE FROM users",
        "/* sneaky */ DROP TABLE x",
        "",
        "   ",
        "-- only a comment",
    ],
)
def test_validate_rejects_non_select(sql: str) -> None:
    with pytest.raises(ReportQueryError, match="must start with SELECT or WITH"):
        _validate_select_only(sql)


@pytest.mark.asyncio
async def test_describe_report_requires_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    """On SQLite, describe_report refuses with a PostgreSQL hint (no DB access)."""
    from clarinet.repositories.report_repository import ReportRepository
    from clarinet.settings import DatabaseDriver, settings

    monkeypatch.setattr(settings, "database_driver", DatabaseDriver.SQLITE)
    with pytest.raises(ReportQueryError, match="PostgreSQL"):
        await ReportRepository().describe_report("SELECT 1")


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("CLARINET_TEST_DATABASE_URL"),
    reason="requires PostgreSQL (CLARINET_TEST_DATABASE_URL); runs in test-all-stages stage 6",
)
async def test_describe_report_reads_postgres_types() -> None:
    """The real asyncpg prepare()/get_attributes() path maps PG types correctly.

    Cast-only literals → a plannable query that needs no tables, so the test
    asserts the SQLAlchemy→asyncpg bridge and the type names independently of
    any schema.
    """
    from unittest.mock import PropertyMock, patch

    from clarinet.repositories.report_repository import ReportRepository
    from clarinet.settings import DatabaseDriver, Settings, settings
    from clarinet.utils.db_manager import DatabaseManager

    pg_url = os.environ["CLARINET_TEST_DATABASE_URL"]
    with (
        patch.object(Settings, "database_url", new_callable=PropertyMock, return_value=pg_url),
        patch.object(settings, "database_driver", DatabaseDriver.POSTGRESQL_ASYNC),
    ):
        manager = DatabaseManager()
        try:
            columns = await ReportRepository(manager=manager).describe_report(
                "SELECT 1::int4 AS id, 'x'::text AS email, "
                "true AS is_active, now()::timestamptz AS created_at"
            )
        finally:
            await manager.close()

    assert {c.name: c.pg_type for c in columns} == {
        "id": "int4",
        "email": "text",
        "is_active": "bool",
        "created_at": "timestamptz",
    }
