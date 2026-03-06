"""Lightweight, sync model constructors for integration tests.

These helpers create model *instances* without touching the database.
For async factories that create + commit, see ``test_helpers.py``.
"""

from datetime import UTC, datetime
from uuid import uuid4

from src.models.base import DicomQueryLevel
from src.models.patient import Patient
from src.models.record import Record, RecordType, RecordTypeCreate
from src.models.study import Series, Study
from src.models.user import User
from src.utils.auth import get_password_hash


def make_patient(pid: str = "PAT_001", name: str = "Alice") -> Patient:
    """Create a Patient instance (not persisted)."""
    return Patient(id=pid, name=name)


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


def make_record_type(name: str = "test_rt_00001", **kw: object) -> RecordType:
    """Create a RecordType instance (not persisted)."""
    defaults: dict[str, object] = {"name": name, "level": DicomQueryLevel.SERIES}
    defaults.update(kw)
    return RecordType(**defaults)


async def seed_record(session, patient_id, study_uid, series_uid, rt_name, **kw):
    """Create a Record directly in the session (bypasses model validator)."""
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
