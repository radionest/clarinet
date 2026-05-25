"""
Patient-related models for the Clarinet framework.

This module provides models for patients, studies, and series.
"""

import re
from typing import TYPE_CHECKING, Any

from pydantic import computed_field, field_validator
from sqlmodel import Column, Field, Integer, Relationship

from ..exceptions.domain import InvalidPatientIdentifierError
from ..settings import settings
from ..utils.logger import logger
from .base import BaseModel

if TYPE_CHECKING:
    from .record import Record
    from .study import Study, StudyRead


# DICOM PatientID (0010,0020), VR=LO. Allowed chars: A-Z a-z 0-9 . _ - ^
# DICOM caps LO at 64 chars. Whitespace is stripped before this check.
PATIENT_ID_PATTERN = re.compile(r"[A-Za-z0-9._\-^]{1,64}")


def normalize_patient_id(raw: str) -> str:
    """Trim whitespace and validate a patient ID per DICOM LO PatientID format.

    Logs at INFO when whitespace stripping changed the value — visible
    asymmetry between client input and stored value otherwise hides
    URL-builder bugs in callers.

    Raises:
        InvalidPatientIdentifierError: if the trimmed value is empty
            or violates the DICOM character/length constraints.
    """
    if not isinstance(raw, str):
        raise InvalidPatientIdentifierError(str(raw), "must be a string")
    cleaned = raw.strip()
    if not cleaned:
        raise InvalidPatientIdentifierError(raw, "empty after trimming whitespace")
    if not PATIENT_ID_PATTERN.fullmatch(cleaned):
        raise InvalidPatientIdentifierError(
            raw,
            "must match DICOM LO PatientID format (A-Z a-z 0-9 . _ - ^, max 64 chars)",
        )
    if cleaned != raw:
        logger.info(
            f"patient_id normalized: stripped whitespace (len {len(raw)} -> {len(cleaned)})"
        )
    return cleaned


class PatientBase(BaseModel):
    """Core patient fields — no auto_id (used by PatientSave)."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(default=None, min_length=1, max_length=64)
    anon_name: str | None = Field(default=None, min_length=5, max_length=50, unique=True)

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, v: Any) -> Any:
        """Defence-in-depth: any path that instantiates a Patient model
        (direct ORM construction, fixtures, model_validate) gets the
        same trim + DICOM-format check the service layer applies."""
        if v is None or not isinstance(v, str):
            return v
        return normalize_patient_id(v)


class PatientInfo(PatientBase):
    """Patient fields with auto_id and computed anon_id.

    Used as the embedded patient type in StudyRead/RecordRead responses,
    and as the base for Patient (ORM) and PatientRead.
    """

    auto_id: int | None = Field(default=None)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def anon_id(self) -> str | None:
        """Generate an anonymous ID using the auto_id.

        Note: @property is required so that mypy resolves the return type as
        ``str | None`` instead of ``Callable[[], str | None]``.  This is an
        upstream mypy limitation (pydantic#11687).  Do NOT remove @property.
        """
        if self.auto_id is None:
            return None
        return f"{settings.anon_id_prefix}_{self.auto_id}"


class Patient(PatientInfo, table=True):
    """Model representing a patient in the system."""

    id: str = Field(
        primary_key=True,
        min_length=1,
        max_length=64,
    )
    studies: list["Study"] = Relationship(back_populates="patient", cascade_delete=True)
    auto_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            nullable=False,
            unique=True,
        ),
    )
    records: list["Record"] = Relationship(back_populates="patient", cascade_delete=True)

    # Note: SQLModel ``table=True`` classes bypass Pydantic field
    # validators in ``__init__``. The normalize check on this class only
    # fires through ``Patient.model_validate(...)``; direct
    # ``Patient(id=...)`` construction (tests, fixtures, low-level repo
    # code) must call ``normalize_patient_id`` explicitly. All API paths
    # already do so via :class:`StudyService` and ``PatientSave``.


class PatientSave(PatientBase):
    """Pydantic model for creating a new patient."""

    id: str = Field(min_length=1, max_length=64, alias="patient_id")
    name: str = Field(min_length=1, max_length=64, alias="patient_name")

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, v: Any) -> Any:
        """Alias re-declaration drops the parent validator — re-attach."""
        if v is None or not isinstance(v, str):
            return v
        return normalize_patient_id(v)


class PatientRead(PatientInfo):
    """Pydantic model for reading patient data with related studies."""

    studies: list["StudyRead"] = Field()
