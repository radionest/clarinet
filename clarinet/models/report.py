"""Response and request schemas for custom SQL report endpoints."""

from enum import Enum

from pydantic import BaseModel as PydanticBaseModel


class ReportFormat(str, Enum):
    """Output format for a generated report."""

    CSV = "csv"
    XLSX = "xlsx"

    @property
    def media_type(self) -> str:
        """HTTP Content-Type for the rendered report."""
        return _FORMAT_INFO[self][0]

    @property
    def extension(self) -> str:
        """File extension used in the download filename."""
        return _FORMAT_INFO[self][1]


# (media_type, extension) per format. Kept next to the enum so adding a new
# format is a one-place change.
_FORMAT_INFO: dict["ReportFormat", tuple[str, str]] = {
    ReportFormat.CSV: ("text/csv; charset=utf-8", "csv"),
    ReportFormat.XLSX: (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "xlsx",
    ),
}


class ReportTemplate(PydanticBaseModel):
    """Metadata for a SQL report shown to admins in the UI."""

    name: str
    title: str
    description: str
