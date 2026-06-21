"""Response and request schemas for Quarto report endpoints.

Quarto reports are ``*.qmd`` documents (Markdown + executable Python chunks)
rendered to DOCX/PDF in the background. Unlike the SQL ``reports`` feature
there is no DB table: render state lives in a ``status.json`` sidecar next to
the output file, serialized here as :class:`QuartoRenderState`.
"""

from enum import Enum

from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field


class QuartoReportFormat(str, Enum):
    """Output format for a rendered Quarto report."""

    DOCX = "docx"
    PDF = "pdf"

    @property
    def media_type(self) -> str:
        """HTTP Content-Type for the rendered file."""
        return _FORMAT_INFO[self][0]

    @property
    def extension(self) -> str:
        """File extension used in the download filename."""
        return _FORMAT_INFO[self][1]


# (media_type, extension) per format. Kept next to the enum so adding a new
# format is a one-place change.
_FORMAT_INFO: dict["QuartoReportFormat", tuple[str, str]] = {
    QuartoReportFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx",
    ),
    QuartoReportFormat.PDF: ("application/pdf", "pdf"),
}


class QuartoRenderStatus(str, Enum):
    """Lifecycle state of a single render, persisted in the status sidecar."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class QuartoReportKind(str, Enum):
    """Whether a Quarto report is a single ``.qmd`` file or a multi-file book project."""

    FILE = "file"
    BOOK = "book"


class QuartoReportTemplate(PydanticBaseModel):
    """Metadata for a Quarto report shown to admins in the UI."""

    name: str
    title: str
    description: str
    # Names of SQL reports the .qmd front matter declares under ``clarinet.data``.
    # Each is executed and materialized as ``data/<name>.csv`` before rendering.
    data_reports: list[str]
    # FILE = single .qmd (front-matter metadata); BOOK = directory with _quarto.yml.
    kind: QuartoReportKind = QuartoReportKind.FILE
    # Extra files the .qmd front matter declares under ``clarinet.stage`` (paths
    # relative to the .qmd). Each is staged flat into the sandbox render dir so a
    # chunk can import a project helper module — and its non-sibling deps — that
    # is not the generated ``report_schemas.py``. Empty when undeclared.
    stage_files: list[str] = Field(default_factory=list)


class QuartoRenderRequest(PydanticBaseModel):
    """Body for ``POST /api/admin/quarto-reports/{name}/render``."""

    # min_length guards against rendering "into nothing": an empty list would
    # complete as DONE with no output files, leaving download a permanent 409.
    formats: list[QuartoReportFormat] = Field(default=[QuartoReportFormat.DOCX], min_length=1)


class QuartoRenderState(PydanticBaseModel):
    """Serialized ``status.json`` sidecar returned by the status endpoint.

    ``ready`` maps each requested format value (``"docx"``/``"pdf"``) to whether
    its output file is on disk, so the UI can offer a download link per format
    as soon as it appears.
    """

    name: str
    render_id: str
    status: QuartoRenderStatus
    formats: list[QuartoReportFormat]
    ready: dict[str, bool]
    error: str | None = None
    created_at: str
    finished_at: str | None = None
