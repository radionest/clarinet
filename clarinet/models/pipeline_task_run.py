"""Pipeline task run audit model.

One row per pipeline task execution, written by ``AuditMiddleware`` over the
HTTP API. Status values: ``running`` | ``succeeded`` | ``failed`` | ``retrying``
(plain strings — no DB enum, so downstream migrations stay additive).
"""

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import field_serializer, field_validator
from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlmodel import Column, Field, SQLModel

from clarinet.types import DbNonNegativeInt32, DbPositiveInt32, PortableJSON

# DB column stays a plain string (additive downstream migrations);
# API payloads are constrained to these values.
type PipelineRunStatus = Literal["running", "succeeded", "failed", "retrying"]


class PipelineTaskRunBase(SQLModel):
    """Shared fields for pipeline task run audit records.

    ``record_id`` / ``patient_id`` use ``ondelete=SET NULL`` so audit rows
    survive deletion of the entities they reference.
    """

    task_name: str = Field(max_length=255)
    queue: str = Field(max_length=255)
    pipeline_id: str | None = Field(default=None, max_length=100)
    step_index: int | None = Field(default=None)
    record_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("record.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    patient_id: str | None = Field(
        default=None,
        sa_column=Column(
            String(64),
            ForeignKey("patient.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    study_uid: str | None = Field(default=None, max_length=255)
    series_uid: str | None = Field(default=None, max_length=255)
    status: str = Field(
        default="running",
        max_length=20,
        sa_column_kwargs={"server_default": "running"},
    )
    started_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    execution_time: float | None = Field(default=None)
    retry_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    error_type: str | None = Field(default=None, max_length=255)
    error_message: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    error_status_code: int | None = Field(default=None)
    result: dict[str, Any] | None = Field(
        default=None, sa_column=Column(PortableJSON, nullable=True)
    )


class PipelineTaskRun(PipelineTaskRunBase, table=True):
    """Audit record for a single pipeline task execution.

    Primary key is the TaskIQ ``message.task_id`` (36-char UUID string) —
    no separate surrogate key, so middleware writes are idempotent.
    """

    __tablename__ = "pipeline_task_run"
    __table_args__ = (
        Index("ix_pipeline_task_run_status_started_at", "status", "started_at"),
        Index("ix_pipeline_task_run_pipeline_id_step", "pipeline_id", "step_index"),
    )

    id: str = Field(sa_column=Column(String(36), primary_key=True))
    created_at: datetime = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
            onupdate=func.now(),
        ),
    )


class PipelineTaskRunCreate(SQLModel):
    """Payload for ``POST /api/pipelines/runs`` (AuditMiddleware ``pre_execute``)."""

    id: str = Field(min_length=1, max_length=36)
    task_name: str = Field(min_length=1, max_length=255)
    queue: str = Field(max_length=255)
    pipeline_id: str | None = Field(default=None, max_length=100)
    step_index: DbNonNegativeInt32 | None = None
    record_id: DbPositiveInt32 | None = None
    patient_id: str | None = Field(default=None, max_length=64)
    study_uid: str | None = Field(default=None, max_length=255)
    series_uid: str | None = Field(default=None, max_length=255)
    started_at: datetime

    @field_validator("patient_id", "study_uid", "series_uid", mode="before")
    def _empty_identifiers_to_none(cls, value: str | None) -> str | None:
        """Normalize the '' PipelineMessage sentinel to NULL so it can't hit the patient FK.

        Deliberately NOT inherited from clarinet BaseModel: blanket empty_to_none
        would also null the required ``queue`` field (workers legitimately send "")
        and reject the whole audit row.
        """
        return value or None


class PipelineTaskRunUpdate(SQLModel):
    """Payload for ``PATCH /api/pipelines/runs/{task_id}`` (``post_execute``)."""

    status: PipelineRunStatus
    finished_at: datetime
    execution_time: float | None = None
    retry_count: DbNonNegativeInt32 | None = None
    error_type: str | None = Field(default=None, max_length=255)
    error_message: str | None = None
    error_status_code: DbNonNegativeInt32 | None = None
    result: dict[str, Any] | None = None


class PipelineTaskRunRead(PipelineTaskRunBase):
    """API response schema for pipeline task runs."""

    id: str
    created_at: datetime
    updated_at: datetime

    @field_serializer("started_at", "finished_at", "created_at", "updated_at", when_used="json")
    def _ensure_tz_aware(self, value: datetime | None) -> datetime | None:
        """Re-attach UTC to naive timestamps so the response is RFC 3339 ``date-time``.

        SQLite ``DateTime`` drops ``tzinfo`` on the read-back after insert, so a
        freshly-created row serializes as ``2026-06-10T08:51:25`` (no offset),
        which violates the OpenAPI ``format: date-time`` schema. PostgreSQL keeps
        the offset; this only bites the SQLite-backed schema tests, but stored
        timestamps are always UTC, so re-attaching it here is correct everywhere.
        """
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class PipelineTaskRunFind(SQLModel):
    """Search filters for pipeline task runs (all optional).

    ``since`` is a lower bound on ``started_at`` (results are also ordered
    by ``started_at``, newest first).
    """

    status: PipelineRunStatus | None = None
    task_name: str | None = None
    record_id: int | None = None
    patient_id: str | None = None
    since: datetime | None = None
    skip: int = 0
    limit: int = 100
