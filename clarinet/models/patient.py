"""
Patient-related models for the Clarinet framework.

This module provides models for patients, studies, and series.
"""

import re
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import StringConstraints, computed_field, field_validator
from sqlmodel import Column, Field, Integer, Relationship

from ..exceptions.domain import InvalidPatientIdentifierError
from ..settings import settings
from .base import BaseModel

if TYPE_CHECKING:
    from .record import Record
    from .study import Study, StudyRead


# DICOM PatientID (0010,0020), VR=LO. Allowed chars: A-Z a-z 0-9 . _ - ^
# DICOM caps LO at 64 chars. Strict — no whitespace allowed; trim is the
# client's responsibility (the Gleam form does this, and curl/CLI users
# must strip explicitly).
PATIENT_ID_REGEX = r"^[A-Za-z0-9._\-^]{1,64}$"
PATIENT_ID_PATTERN = re.compile(PATIENT_ID_REGEX)

# Annotated type — surfaces the pattern in JSON Schema / OpenAPI so
# clients (and Schemathesis) see the real contract. Mirrors the
# ``DicomUID`` pattern in ``base.py``.
PatientID = Annotated[
    str,
    StringConstraints(min_length=1, max_length=64, pattern=PATIENT_ID_REGEX),
]


def validate_patient_id(raw: str) -> str:
    """Validate a patient ID per DICOM LO PatientID format.

    Used for path parameters and dict-input from non-Pydantic call
    sites. Pydantic ``Field(regex=PATIENT_ID_REGEX)`` covers the
    typed-DTO entry paths and raises the standard FastAPI 422; this
    helper raises the domain-typed
    :class:`InvalidPatientIdentifierError` so structured error bodies
    stay consistent across path and body inputs.

    Raises:
        InvalidPatientIdentifierError: if the value is not a string,
            is empty, or violates the DICOM character/length constraints.
    """
    if not isinstance(raw, str):
        raise InvalidPatientIdentifierError(str(raw), "must be a string")
    if not raw:
        raise InvalidPatientIdentifierError(raw, "must not be empty")
    if not PATIENT_ID_PATTERN.fullmatch(raw):
        raise InvalidPatientIdentifierError(
            raw,
            "must match DICOM LO PatientID format (A-Z a-z 0-9 . _ - ^, max 64 chars)",
        )
    return raw


class PatientBase(BaseModel):
    """Core patient fields — no auto_id (used by PatientSave)."""

    id: PatientID
    name: str = Field(default=None, min_length=1, max_length=64)
    anon_name: str | None = Field(default=None, min_length=5, max_length=50, unique=True)


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

    id: PatientID = Field(primary_key=True)
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


class PatientSave(PatientBase):
    """Pydantic model for creating a new patient."""

    id: PatientID = Field(alias="patient_id")
    name: str = Field(min_length=1, max_length=64, alias="patient_name")

    # Alias re-declaration drops the parent validator; re-attach so a
    # whitespace-laden body produces the structured domain 422
    # (``code=INVALID_PATIENT_IDENTIFIER``) instead of Pydantic's
    # generic ``string_pattern_mismatch`` envelope.
    @field_validator("id", mode="before")
    @classmethod
    def _validate_id(cls, v: Any) -> Any:
        if v is None or not isinstance(v, str):
            return v
        return validate_patient_id(v)


class PatientRead(PatientInfo):
    """Pydantic model for reading patient data with related studies."""

    studies: list["StudyRead"] = Field()
