"""Repository for executing custom SQL report queries.

Unlike model-bound repositories under :mod:`clarinet.repositories`, this one
runs free-form ``SELECT`` text supplied by the project owner. It opens its
own session via :data:`db_manager` so the caller-scoped session never sees
``SET TRANSACTION READ ONLY`` / ``SET LOCAL statement_timeout`` — those would
leak into unrelated queries on the same connection.
"""

import asyncio
from typing import Any

from sqlalchemy import text

from clarinet.exceptions.domain import ReportQueryError
from clarinet.settings import DatabaseDriver, settings
from clarinet.utils.db_manager import DatabaseManager, db_manager
from clarinet.utils.logger import logger


class ReportRepository:
    """Executes a single report SQL string in a read-only transaction.

    The repository is intentionally stateless and does not extend
    :class:`BaseRepository` — there is no ORM model behind it.
    """

    # Buffer above the SQL-level timeout so PostgreSQL has a chance to raise
    # ``QueryCanceledError`` before asyncio cancels the coroutine. Cancelling
    # mid-statement leaves the connection in an unclean state and forces a
    # full reconnect, which is expensive under load.
    _PYTHON_TIMEOUT_BUFFER_SECONDS: float = 20.0

    def __init__(self, manager: DatabaseManager = db_manager) -> None:
        self._db_manager = manager

    async def execute_report(self, sql: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Run ``sql`` and return ``(columns, rows)``.

        Raises:
            ReportQueryError: SQL failed to execute or the timeout fired.
        """
        sql_timeout = settings.reports_query_timeout_seconds
        wait_timeout = sql_timeout + self._PYTHON_TIMEOUT_BUFFER_SECONDS

        try:
            return await asyncio.wait_for(self._run(sql, sql_timeout), timeout=wait_timeout)
        except TimeoutError as exc:
            logger.error(f"Report query exceeded {wait_timeout}s asyncio timeout")
            raise ReportQueryError("Report query timed out") from exc

    async def _run(
        self,
        sql: str,
        sql_timeout_seconds: int,
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        async with self._db_manager.async_session_factory() as session:
            try:
                await self._apply_safety_pragmas(session, sql_timeout_seconds)
                result = await session.execute(text(sql))
                columns = list(result.keys())
                rows = [tuple(row) for row in result.fetchall()]
            except Exception as exc:
                # Log first, then surface as a typed domain error so the
                # exception handler maps it to a uniform 500 response.
                logger.error(f"Report SQL execution failed: {exc}")
                await session.rollback()
                raise ReportQueryError(f"Report execution failed: {exc}") from exc
            else:
                # Read-only path: discard the transaction explicitly so the
                # connection returns to the pool in a clean state regardless
                # of the autocommit / autobegin behavior of the driver.
                await session.rollback()
                return columns, rows

    @staticmethod
    async def _apply_safety_pragmas(session: Any, sql_timeout_seconds: int) -> None:
        """Set read-only mode and statement timeout on PostgreSQL.

        SQLite does not understand ``SET TRANSACTION READ ONLY`` /
        ``SET LOCAL`` and would raise a syntax error, so the safety pragmas
        are applied only when the configured driver is PostgreSQL. Tests
        running on SQLite still exercise the rest of the code path.
        """
        if settings.database_driver == DatabaseDriver.SQLITE:
            return
        await session.execute(text("SET TRANSACTION READ ONLY"))
        timeout_ms = sql_timeout_seconds * 1000
        await session.execute(text(f"SET LOCAL statement_timeout = {timeout_ms}"))
