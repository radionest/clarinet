"""DTOs for the global search endpoint (POST /api/search)."""

from pydantic import ConfigDict
from sqlmodel import Field, SQLModel

from .patient import PatientRead
from .study import StudyRead


class GlobalSearchQuery(SQLModel):
    """Body of POST /api/search — one free-form query string."""

    model_config = ConfigDict(extra="forbid")  # type: ignore[assignment]

    query: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=25, ge=1, le=100)


class GlobalSearchResults(SQLModel):
    """Grouped results of a global search."""

    patients: list[PatientRead] = Field(default_factory=list)
    studies: list[StudyRead] = Field(default_factory=list)
