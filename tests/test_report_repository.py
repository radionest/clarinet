"""Unit tests for the ReportRepository SQL safety validator."""

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
