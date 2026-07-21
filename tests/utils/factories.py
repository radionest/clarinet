"""Lightweight, sync model constructors for integration tests.

These helpers create model *instances* without touching the database.
For async factories that create + commit, see ``test_helpers.py``.
"""

from datetime import UTC, datetime
from itertools import count
from uuid import uuid4

from clarinet.models.base import DicomQueryLevel
from clarinet.models.file_schema import FileDefinitionRead
from clarinet.models.patient import Patient
from clarinet.models.record import Record, RecordType, RecordTypeCreate
from clarinet.models.study import Series, Study
from clarinet.models.user import User
from clarinet.utils.auth import get_password_hash

_auto_id_seq = count(1)


def next_auto_id() -> int:
    """Return the next unique auto_id for test patients.

    Shared counter used by both :func:`make_patient` and
    :class:`~tests.utils.test_helpers.PatientFactory` to avoid duplicates.
    """
    return next(_auto_id_seq)


def make_patient(
    pid: str = "PAT_001",
    name: str = "Alice",
    auto_id: int | None = None,
    anon_name: str | None = None,
) -> Patient:
    """Create a Patient instance (not persisted).

    Auto-assigns a unique ``auto_id`` when not provided (Patient.auto_id is NOT NULL).
    """
    return Patient(
        id=pid,
        name=name,
        auto_id=auto_id if auto_id is not None else next_auto_id(),
        anon_name=anon_name,
    )


def make_study(patient_id: str, uid: str = "1.2.3.100") -> Study:
    """Create a Study instance (not persisted)."""
    return Study(patient_id=patient_id, study_uid=uid, date=datetime.now(UTC).date())


def make_series(study_uid: str, uid: str = "1.2.3.100.1", num: int = 1) -> Series:
    """Create a Series instance (not persisted)."""
    return Series(study_uid=study_uid, series_uid=uid, series_number=num)


def make_user(**kw: object) -> User:
    """Create a User instance with sensible defaults (not persisted)."""
    defaults: dict[str, object] = {
        "id": uuid4(),
        "email": f"u_{uuid4().hex[:6]}@test.com",
        "hashed_password": get_password_hash("password123"),
        "is_active": True,
        "is_verified": True,
        "is_superuser": False,
    }
    defaults.update(kw)
    return User(**defaults)


def make_record_type(name: str = "test-rt-00001", **kw: object) -> RecordType:
    """Create a RecordType instance (not persisted)."""
    defaults: dict[str, object] = {"name": name, "level": DicomQueryLevel.SERIES}
    defaults.update(kw)
    return RecordType(**defaults)


async def seed_record(session, patient_id, study_uid, series_uid, rt_name, **kw):
    """Create a Record directly in the session (bypasses model validator).

    SQLite test engines enable ``PRAGMA foreign_keys=ON`` (conftest.py) and the
    PostgreSQL test backend enforces FKs natively. Callers must persist the
    referenced Patient / Study / Series / RecordType before calling this helper,
    otherwise the INSERT fails with an FK violation.
    """
    rec = Record(
        patient_id=patient_id,
        study_uid=study_uid,
        series_uid=series_uid,
        record_type_name=rt_name,
        **kw,
    )
    session.add(rec)
    await session.commit()
    await session.refresh(rec)
    return rec


def make_record_type_config(
    name: str,
    description: str = "test",
    level: str = "SERIES",
    **extra: object,
) -> RecordTypeCreate:
    """Create a RecordTypeCreate schema instance for config reconciler tests."""
    return RecordTypeCreate(name=name, description=description, level=level, **extra)


def make_record_type_create(
    name: str,
    level: str = "SERIES",
    *,
    parent_required: bool = False,
    unique_by: frozenset[str] | set[str] | None = None,
    max_records: int | None = None,
    output_pattern: str | None = None,
    multiple: bool = False,
    file_allow_path_collision: bool = False,
    file_level: str | None = None,
    **extra: object,
) -> RecordTypeCreate:
    """Build a RecordTypeCreate with at most one OUTPUT file, for path-uniqueness tests.

    ``output_pattern=None`` (default) omits ``file_registry`` entirely. When given, a
    single OUTPUT ``FileDefinitionRead`` is attached; ``file_level`` overrides the
    file's own DICOM level (defaults to the RecordType's own level, i.e. no
    coarser-than-record-type mismatch).

    Uses ``model_construct`` (bypasses validation): ``RecordTypeCreate`` itself
    calls ``validate_output_path_uniqueness`` in a model validator, so a normal
    constructor call would reject exactly the "should fail" fixtures these
    tests build before the test even gets to call the validator explicitly.
    """
    file_registry = None
    if output_pattern is not None:
        file_registry = [
            FileDefinitionRead(
                name="out_file",
                pattern=output_pattern,
                multiple=multiple,
                allow_path_collision=file_allow_path_collision,
                level=file_level,
            )
        ]
    return RecordTypeCreate.model_construct(
        name=name,
        level=level,
        parent_required=parent_required,
        unique_by=unique_by,
        max_records=max_records,
        file_registry=file_registry,
        **extra,
    )


def make_record_type_create_two_outputs(
    name: str,
    level: str = "SERIES",
    *,
    parent_required: bool = False,
    unique_by: frozenset[str] | set[str] | None = None,
    max_records: int | None = None,
    outputs: list[tuple[str, bool]],
    **extra: object,
) -> RecordTypeCreate:
    """Build a RecordTypeCreate with several OUTPUT files, for path-uniqueness tests.

    ``outputs`` is a list of ``(pattern, allow_path_collision)`` pairs — one
    OUTPUT ``FileDefinitionRead`` per entry, auto-named ``out_0``, ``out_1``, ...

    Uses ``model_construct`` — see :func:`make_record_type_create` for why.
    """
    file_registry = [
        FileDefinitionRead(
            name=f"out_{i}",
            pattern=pattern,
            allow_path_collision=allow_path_collision,
        )
        for i, (pattern, allow_path_collision) in enumerate(outputs)
    ]
    return RecordTypeCreate.model_construct(
        name=name,
        level=level,
        parent_required=parent_required,
        unique_by=unique_by,
        max_records=max_records,
        file_registry=file_registry,
        **extra,
    )
