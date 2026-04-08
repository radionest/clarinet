"""Unit tests for patient data masking (clarinet/api/masking.py)."""

from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from clarinet.api.masking import mask_record_patient_data, mask_records
from clarinet.models.base import DicomQueryLevel
from clarinet.models.patient import PatientInfo
from clarinet.models.record import RecordRead
from clarinet.models.record_type import RecordTypeRead
from clarinet.models.study import SeriesBase, StudyBase
from clarinet.models.user import User
from clarinet.settings import settings
from clarinet.utils.logger import logger


@pytest.fixture
def capture_info_logs() -> Generator[list[str]]:
    """Capture loguru INFO (and above) log messages during a test.

    Yields a list of ``record["message"]`` strings. Audit-level tests need
    INFO, so this is distinct from the ERROR-only ``capture_logs`` fixture
    in ``tests/integration/conftest.py``.
    """
    messages: list[str] = []

    def _sink(message: Any) -> None:
        messages.append(message.record["message"])

    sink_id = logger.add(_sink, level="INFO", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def _make_user(*, is_superuser: bool = False) -> User:
    """Create a test user instance.

    Args:
        is_superuser: Whether the user is a superuser.

    Returns:
        User instance with randomized id.
    """
    return User(
        id=uuid4(),
        email="test@example.com",
        hashed_password="fakehash",
        is_active=True,
        is_superuser=is_superuser,
    )


def _make_record_read(
    *,
    patient_id: str = "REAL_PAT_001",
    patient_name: str = "Real Patient Name",
    anon_name: str | None = "Anon Patient Name",
    auto_id: int | None = 1,
    study_uid: str = "1.2.3.4.5.6.7.8",
    study_anon_uid: str | None = "9.8.7.6.5.4.3.2",
    series_uid: str | None = "1.2.3.4.5.6.7.8.9",
    series_anon_uid: str | None = "9.8.7.6.5.4.3.2.1",
    mask_patient_data: bool = True,
) -> RecordRead:
    """Create a test RecordRead instance.

    Args:
        patient_id: Patient ID.
        patient_name: Patient name.
        anon_name: Anonymized patient name (None = not anonymized).
        auto_id: Auto-generated ID for anon_id computation.
        study_uid: Study UID.
        study_anon_uid: Anonymized study UID (None = not anonymized).
        series_uid: Series UID (None = no series).
        series_anon_uid: Anonymized series UID (None = not anonymized).
        mask_patient_data: Whether the record type masks patient data.

    Returns:
        RecordRead instance with all relationships populated.
    """
    patient = PatientInfo(
        id=patient_id,
        name=patient_name,
        anon_name=anon_name,
        auto_id=auto_id,
    )

    study = StudyBase(
        study_uid=study_uid,
        date=datetime.now(tz=UTC).date(),
        anon_uid=study_anon_uid,
        patient_id=patient_id,
    )

    series = None
    if series_uid:
        series = SeriesBase(
            series_uid=series_uid,
            series_number=1,
            anon_uid=series_anon_uid,
            study_uid=study_uid,
        )

    record_type = RecordTypeRead(
        name="test-type-xxxxx",
        level=DicomQueryLevel.SERIES if series_uid else DicomQueryLevel.STUDY,
        mask_patient_data=mask_patient_data,
    )

    return RecordRead(
        id=1,
        patient_id=patient_id,
        study_uid=study_uid,
        series_uid=series_uid,
        record_type_name="test-type-xxxxx",
        patient=patient,
        study=study,
        series=series,
        record_type=record_type,
    )


class TestMaskRecordPatientData:
    """Tests for mask_record_patient_data function."""

    def test_superuser_sees_all_data(self) -> None:
        """Superusers see original data even when patient is anonymized."""
        superuser = _make_user(is_superuser=True)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=42,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid="9.8.7.6.5.4.3.2.1",
        )

        result = mask_record_patient_data(record, superuser)

        # Superuser sees original data
        assert result.patient_id == "REAL_PAT_001"
        assert result.patient.id == "REAL_PAT_001"
        assert result.patient.name == "Real Patient Name"
        assert result.study_uid == "1.2.3.4.5.6.7.8"
        assert result.study is not None
        assert result.study.study_uid == "1.2.3.4.5.6.7.8"
        assert result.series_uid == "1.2.3.4.5.6.7.8.9"
        assert result.series is not None
        assert result.series.series_uid == "1.2.3.4.5.6.7.8.9"

    def test_non_admin_anonymized_patient_masked(self) -> None:
        """Non-superuser sees masked data when patient is anonymized."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=42,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid="9.8.7.6.5.4.3.2.1",
        )

        result = mask_record_patient_data(record, user)

        # Non-superuser sees anonymized data
        expected_anon_id = f"{settings.anon_id_prefix}_42"
        assert result.patient_id == expected_anon_id
        assert result.patient.id == expected_anon_id
        assert result.patient.name == "Anon Patient Name"
        assert result.study_uid == "9.8.7.6.5.4.3.2"
        assert result.study is not None
        assert result.study.study_uid == "9.8.7.6.5.4.3.2"
        assert result.series_uid == "9.8.7.6.5.4.3.2.1"
        assert result.series is not None
        assert result.series.series_uid == "9.8.7.6.5.4.3.2.1"

    def test_non_admin_non_anonymized_patient_no_masking(self) -> None:
        """Non-superuser sees original data when patient is not anonymized."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name=None,  # Not anonymized
            auto_id=None,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid="9.8.7.6.5.4.3.2.1",
        )

        result = mask_record_patient_data(record, user)

        # Non-superuser sees original data when patient not anonymized
        assert result.patient_id == "REAL_PAT_001"
        assert result.patient.id == "REAL_PAT_001"
        assert result.patient.name == "Real Patient Name"
        assert result.study_uid == "1.2.3.4.5.6.7.8"
        assert result.study is not None
        assert result.study.study_uid == "1.2.3.4.5.6.7.8"
        assert result.series_uid == "1.2.3.4.5.6.7.8.9"
        assert result.series is not None
        assert result.series.series_uid == "1.2.3.4.5.6.7.8.9"

    def test_study_uid_masked_when_anon_uid_exists(self) -> None:
        """Study UID is masked when anon_uid is set."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=1,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid=None,  # No series
            series_anon_uid=None,
        )

        result = mask_record_patient_data(record, user)

        # Study UID is masked
        assert result.study_uid == "9.8.7.6.5.4.3.2"
        assert result.study is not None
        assert result.study.study_uid == "9.8.7.6.5.4.3.2"

    def test_study_uid_not_masked_when_anon_uid_none(self) -> None:
        """Study UID is not masked when anon_uid is None."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=1,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid=None,  # No anon_uid
            series_uid=None,
            series_anon_uid=None,
        )

        result = mask_record_patient_data(record, user)

        # Study UID is not masked
        assert result.study_uid == "1.2.3.4.5.6.7.8"
        assert result.study is not None
        assert result.study.study_uid == "1.2.3.4.5.6.7.8"

    def test_series_uid_masked_when_anon_uid_exists(self) -> None:
        """Series UID is masked when anon_uid is set."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=1,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid="9.8.7.6.5.4.3.2.1",
        )

        result = mask_record_patient_data(record, user)

        # Series UID is masked
        assert result.series_uid == "9.8.7.6.5.4.3.2.1"
        assert result.series is not None
        assert result.series.series_uid == "9.8.7.6.5.4.3.2.1"

    def test_series_uid_not_masked_when_anon_uid_none(self) -> None:
        """Series UID is not masked when anon_uid is None."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=1,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid=None,  # No anon_uid
        )

        result = mask_record_patient_data(record, user)

        # Series UID is not masked
        assert result.series_uid == "1.2.3.4.5.6.7.8.9"
        assert result.series is not None
        assert result.series.series_uid == "1.2.3.4.5.6.7.8.9"

    def test_no_study_no_series_masking(self) -> None:
        """Record without study/series still masks patient data."""
        user = _make_user(is_superuser=False)

        # Create a patient-level record (no study/series)
        patient = PatientInfo(
            id="REAL_PAT_001",
            name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=1,
        )

        record_type = RecordTypeRead(
            name="patient-type",
            level=DicomQueryLevel.PATIENT,
        )

        record = RecordRead(
            id=1,
            patient_id="REAL_PAT_001",
            study_uid=None,
            series_uid=None,
            record_type_name="patient-type",
            patient=patient,
            study=None,
            series=None,
            record_type=record_type,
        )

        result = mask_record_patient_data(record, user)

        # Patient data is masked, study/series remain None
        expected_anon_id = f"{settings.anon_id_prefix}_1"
        assert result.patient_id == expected_anon_id
        assert result.patient.id == expected_anon_id
        assert result.patient.name == "Anon Patient Name"
        assert result.study_uid is None
        assert result.study is None
        assert result.series_uid is None
        assert result.series is None

    def test_anon_id_none_when_auto_id_none(self) -> None:
        """When auto_id is None, anon_id is None and patient_id is not masked."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=None,  # No auto_id
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid=None,
            series_anon_uid=None,
        )

        result = mask_record_patient_data(record, user)

        # Patient name is masked, but patient_id remains original (anon_id is None)
        assert result.patient_id == "REAL_PAT_001"
        assert result.patient.id == "REAL_PAT_001"
        assert result.patient.name == "Anon Patient Name"

    def test_record_type_opted_out_not_masked(self, capture_info_logs: list[str]) -> None:
        """``mask_patient_data=False`` bypasses masking and writes audit log."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=42,
            mask_patient_data=False,
        )

        result = mask_record_patient_data(record, user)

        # Real data returned despite anonymized patient
        assert result.patient_id == "REAL_PAT_001"
        assert result.patient.id == "REAL_PAT_001"
        assert result.patient.name == "Real Patient Name"
        assert result.study_uid == "1.2.3.4.5.6.7.8"
        assert result.study is not None
        assert result.study.study_uid == "1.2.3.4.5.6.7.8"
        assert result.series_uid == "1.2.3.4.5.6.7.8.9"
        assert result.series is not None
        assert result.series.series_uid == "1.2.3.4.5.6.7.8.9"

        # Audit log contains identifiers only — no PII leakage
        audit_lines = [m for m in capture_info_logs if "deanon_access" in m]
        assert len(audit_lines) == 1
        audit_msg = audit_lines[0]
        assert f"user_id={user.id}" in audit_msg
        assert "record_id=1" in audit_msg
        assert "record_type=test-type-xxxxx" in audit_msg
        # PII must never leak into audit logs
        assert "Real Patient Name" not in audit_msg
        assert "REAL_PAT_001" not in audit_msg
        assert "1.2.3.4.5.6.7.8" not in audit_msg

    def test_record_type_masked_explicit_default(self) -> None:
        """Explicit ``mask_patient_data=True`` behaves identically to the default."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=42,
            mask_patient_data=True,
        )

        result = mask_record_patient_data(record, user)

        # Standard masking applies
        assert result.patient.name == "Anon Patient Name"
        assert result.patient_id == f"{settings.anon_id_prefix}_42"

    def test_superuser_bypasses_mask_patient_data_flag(self, capture_info_logs: list[str]) -> None:
        """Superusers always see real data regardless of the opt-out flag."""
        superuser = _make_user(is_superuser=True)

        # Case 1: opt-out flag set — no audit log (superuser short-circuits first)
        record_opted_out = _make_record_read(
            anon_name="Anon Name", auto_id=1, mask_patient_data=False
        )
        result = mask_record_patient_data(record_opted_out, superuser)
        assert result.patient.name == "Real Patient Name"

        # Case 2: flag default (True) — still sees real data
        record_default = _make_record_read(anon_name="Anon Name", auto_id=1, mask_patient_data=True)
        result = mask_record_patient_data(record_default, superuser)
        assert result.patient.name == "Real Patient Name"

        # No audit log for superuser access
        assert not any("deanon_access" in m for m in capture_info_logs)


class TestMaskRecords:
    """Tests for mask_records function."""

    def test_mask_records_batch(self) -> None:
        """mask_records processes a list of Record ORM objects."""
        user = _make_user(is_superuser=False)

        # Create Record ORM objects (not RecordRead)
        # These would normally be fetched from DB, but we construct them directly
        patient1 = PatientInfo(
            id="PAT_001",
            name="Patient One",
            anon_name="Anon One",
            auto_id=1,
        )
        patient2 = PatientInfo(
            id="PAT_002",
            name="Patient Two",
            anon_name="Anon Two",
            auto_id=2,
        )

        study1 = StudyBase(
            study_uid="1.2.3.4.5.6.7.8.1",
            date=datetime.now(tz=UTC).date(),
            anon_uid="9.8.7.6.5.4.3.2.1",
            patient_id="PAT_001",
        )
        study2 = StudyBase(
            study_uid="1.2.3.4.5.6.7.8.2",
            date=datetime.now(tz=UTC).date(),
            anon_uid="9.8.7.6.5.4.3.2.2",
            patient_id="PAT_002",
        )

        record_type = RecordTypeRead(
            name="test-type-xxxxx",
            level=DicomQueryLevel.STUDY,
        )

        # Use RecordRead instead of Record since we can't easily construct
        # Record ORM objects without a DB session
        record1 = RecordRead(
            id=1,
            patient_id="PAT_001",
            study_uid="1.2.3.4.5.6.7.8.1",
            series_uid=None,
            record_type_name="test-type-xxxxx",
            patient=patient1,
            study=study1,
            series=None,
            record_type=record_type,
        )

        record2 = RecordRead(
            id=2,
            patient_id="PAT_002",
            study_uid="1.2.3.4.5.6.7.8.2",
            series_uid=None,
            record_type_name="test-type-xxxxx",
            patient=patient2,
            study=study2,
            series=None,
            record_type=record_type,
        )

        # mask_records expects Record objects, but since we're using RecordRead
        # in this test (to avoid DB dependency), we cast them
        # In real usage, these would be Record ORM objects from a query
        results = mask_records([record1, record2], user)  # type: ignore[arg-type, list-item]

        assert len(results) == 2

        # First record masked
        assert results[0].patient_id == f"{settings.anon_id_prefix}_1"
        assert results[0].patient.name == "Anon One"
        assert results[0].study_uid == "9.8.7.6.5.4.3.2.1"

        # Second record masked
        assert results[1].patient_id == f"{settings.anon_id_prefix}_2"
        assert results[1].patient.name == "Anon Two"
        assert results[1].study_uid == "9.8.7.6.5.4.3.2.2"

    def test_mask_records_empty_list(self) -> None:
        """mask_records handles empty list correctly."""
        user = _make_user(is_superuser=False)
        results = mask_records([], user)
        assert results == []

    def test_mask_records_with_superuser(self) -> None:
        """mask_records with superuser returns unmasked data."""
        superuser = _make_user(is_superuser=True)

        patient = PatientInfo(
            id="PAT_001",
            name="Patient One",
            anon_name="Anon One",
            auto_id=1,
        )

        study = StudyBase(
            study_uid="1.2.3.4.5.6.7.8",
            date=datetime.now(tz=UTC).date(),
            anon_uid="9.8.7.6.5.4.3.2",
            patient_id="PAT_001",
        )

        record_type = RecordTypeRead(
            name="test-type-xxxxx",
            level=DicomQueryLevel.STUDY,
        )

        record = RecordRead(
            id=1,
            patient_id="PAT_001",
            study_uid="1.2.3.4.5.6.7.8",
            series_uid=None,
            record_type_name="test-type-xxxxx",
            patient=patient,
            study=study,
            series=None,
            record_type=record_type,
        )

        results = mask_records([record], superuser)  # type: ignore[arg-type, list-item]

        assert len(results) == 1
        # Superuser sees original data
        assert results[0].patient_id == "PAT_001"
        assert results[0].patient.name == "Patient One"
        assert results[0].study_uid == "1.2.3.4.5.6.7.8"
