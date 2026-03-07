"""Pipeline definition model for DB-backed pipeline chain definitions."""

from sqlmodel import JSON, Column, Field, SQLModel


class PipelineDefinitionBase(SQLModel):
    """Shared fields for pipeline definitions.

    Args:
        name: Unique pipeline identifier (primary key).
        steps: Ordered list of step dicts, each with ``task_name`` and ``queue``.
    """

    name: str = Field(primary_key=True, min_length=1, max_length=100)
    steps: list[dict[str, str]] = Field(default_factory=list, sa_column=Column(JSON))


class PipelineDefinition(PipelineDefinitionBase, table=True):
    """Stored pipeline chain definition."""

    __tablename__ = "pipeline_definition"


class PipelineDefinitionRead(PipelineDefinitionBase):
    """API response schema for pipeline definitions."""
