"""Service layer for custom SQL reports — discovery + orchestration."""

import io
from collections.abc import Sequence

from clarinet.exceptions.domain import ReportNotFoundError
from clarinet.models.report import ReportFormat, ReportTemplate
from clarinet.repositories.report_repository import ReportRepository
from clarinet.utils.report_discovery import DiscoveredReport
from clarinet.utils.report_formatters import to_csv, to_xlsx


class ReportRegistry:
    """Immutable in-memory registry of SQL templates.

    Built once at startup from :func:`discover_report_templates` and stored on
    ``app.state.report_registry``. Restart the API to pick up new files —
    matches how RecordType / hydrators / file_registry are loaded.
    """

    def __init__(self, items: Sequence[DiscoveredReport]) -> None:
        self._templates: list[ReportTemplate] = [template for template, _ in items]
        self._sql_by_name: dict[str, str] = {t.name: sql for t, sql in items}

    def list_templates(self) -> list[ReportTemplate]:
        return list(self._templates)

    def get_sql(self, name: str) -> str | None:
        return self._sql_by_name.get(name)


class ReportService:
    """Coordinates listing, executing and serializing SQL reports."""

    def __init__(self, registry: ReportRegistry, repo: ReportRepository) -> None:
        self._registry = registry
        self._repo = repo

    def list_reports(self) -> list[ReportTemplate]:
        return self._registry.list_templates()

    async def generate_report(
        self,
        name: str,
        report_format: ReportFormat,
    ) -> tuple[io.BytesIO, str]:
        """Execute the report identified by ``name`` and serialize its result.

        Returns:
            ``(buffer, media_type)`` ready for ``StreamingResponse``.

        Raises:
            ReportNotFoundError: ``name`` is not in the registry.
            ReportQueryError: SQL execution failed or timed out (propagated
                from :class:`ReportRepository`).
        """
        sql = self._registry.get_sql(name)
        if sql is None:
            raise ReportNotFoundError(name)

        columns, rows = await self._repo.execute_report(sql)
        buffer = (
            to_csv(columns, rows) if report_format is ReportFormat.CSV else to_xlsx(columns, rows)
        )
        return buffer, report_format.media_type
