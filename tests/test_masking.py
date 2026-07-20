"""Unit tests for patient data masking (clarinet/api/masking.py)."""

import hashlib
from collections.abc import Generator
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest

from clarinet.api.masking import (
    _MASKED_STUDY_DATE,
    mask_record_patient_data,
    mask_records,
)
from clarinet.models.base import DicomQueryLevel
from clarinet.models.patient import PatientInfo
from clarinet.models.record import RecordRead
from clarinet.models.record_type import RecordTypeRead
from clarinet.models.study import SeriesBase, StudyBase
from clarinet.models.user import User
from clarinet.services.dicom.anonymizer import compute_per_study_patient_id
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
    study_date: date = date(2025, 6, 15),
    study_description: str | None = None,
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
        date=study_date,
        anon_uid=study_anon_uid,
        study_description=study_description,
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
        # Nested study.patient_id must not leak the real patient ID
        assert result.study.patient_id == expected_anon_id
        assert result.series_uid == "9.8.7.6.5.4.3.2.1"
        assert result.series is not None
        assert result.series.series_uid == "9.8.7.6.5.4.3.2.1"
        # Nested series.study_uid must point at the anon study, not the real one
        assert result.series.study_uid == "9.8.7.6.5.4.3.2"

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

    def test_study_date_masked_to_sentinel_when_masked(self) -> None:
        """Masked study date is replaced with the sentinel — real date must not leak."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            anon_name="Anon Patient Name",
            auto_id=1,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            study_date=date(2025, 1, 17),
            series_uid=None,
            series_anon_uid=None,
        )

        result = mask_record_patient_data(record, user)

        assert result.study is not None
        assert result.study.date == _MASKED_STUDY_DATE

    def test_study_date_not_masked_for_superuser(self) -> None:
        """Superusers see the real study date."""
        superuser = _make_user(is_superuser=True)
        record = _make_record_read(
            anon_name="Anon Patient Name",
            auto_id=1,
            study_date=date(2025, 1, 17),
        )

        result = mask_record_patient_data(record, superuser)

        assert result.study is not None
        assert result.study.date == date(2025, 1, 17)

    def test_study_date_preserved_when_opted_out(self) -> None:
        """``mask_patient_data=False`` keeps the real study date for specialist roles."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            anon_name="Anon Patient Name",
            auto_id=1,
            study_date=date(2025, 1, 17),
            mask_patient_data=False,
        )

        result = mask_record_patient_data(record, user)

        assert result.study is not None
        assert result.study.date == date(2025, 1, 17)

    def test_study_date_preserved_when_not_anonymized(self) -> None:
        """Non-anonymized patient: study date is shown as-is to non-superusers."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            anon_name=None,  # not anonymized
            auto_id=None,
            study_date=date(2025, 1, 17),
        )

        result = mask_record_patient_data(record, user)

        assert result.study is not None
        assert result.study.date == date(2025, 1, 17)

    def test_study_date_preserved_when_study_not_masked(self) -> None:
        """Study without anon_uid is not masked — date stays real (consistent with UID)."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            anon_name="Anon Patient Name",
            auto_id=1,
            study_anon_uid=None,  # study not anonymized → UID + date untouched
            study_date=date(2025, 1, 17),
            series_uid=None,
            series_anon_uid=None,
        )

        result = mask_record_patient_data(record, user)

        assert result.study is not None
        assert result.study.study_uid == "1.2.3.4.5.6.7.8"
        assert result.study.date == date(2025, 1, 17)

    def test_study_description_dropped_when_masked(self) -> None:
        """Free-text StudyDescription (potential PHI) is dropped on the masked path."""
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            anon_name="Anon Patient Name",
            auto_id=1,
            study_anon_uid="9.8.7.6.5.4.3.2",
            study_description="CT SCAN PART - BATCH 001",
            series_uid=None,
            series_anon_uid=None,
        )

        result = mask_record_patient_data(record, user)

        assert result.study is not None
        assert result.study.study_description is None

    def test_study_description_preserved_for_superuser(self) -> None:
        """Superusers see the real StudyDescription."""
        superuser = _make_user(is_superuser=True)
        record = _make_record_read(
            anon_name="Anon Patient Name",
            auto_id=1,
            study_description="CT SCAN PART - BATCH 001",
        )

        result = mask_record_patient_data(record, superuser)

        assert result.study is not None
        assert result.study.study_description == "CT SCAN PART - BATCH 001"

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

    def test_nested_parent_identifiers_also_masked(self) -> None:
        """Nested study.patient_id and series.study_uid are masked too.

        Regression: previously only top-level + own UIDs were rewritten, so a
        non-superuser still saw the real patient ID via ``record.study.patient_id``
        and the real study UID via ``record.series.study_uid`` — leaking PII and
        making the response internally inconsistent on the anon path.
        """
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=7,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid="9.8.7.6.5.4.3.2.1",
        )

        result = mask_record_patient_data(record, user)

        expected_anon_id = f"{settings.anon_id_prefix}_7"
        # No real identifiers anywhere in the response
        assert result.study is not None
        assert result.study.patient_id == expected_anon_id
        assert result.study.study_uid == "9.8.7.6.5.4.3.2"
        assert result.series is not None
        assert result.series.study_uid == "9.8.7.6.5.4.3.2"
        assert result.series.series_uid == "9.8.7.6.5.4.3.2.1"

    def test_anon_study_drops_series_without_anon_uid(self) -> None:
        """Series without anon_uid is dropped when the study is anonymized.

        Real scenario: SeriesFilter excluded the series during anonymization
        (localizer, low instance count, ...). Leaking the original
        SeriesInstanceUID under the anonymized StudyInstanceUID would cause
        OHIF to crash in HangingProtocolService because the DICOM metadata
        carries the original (MRI) StudyInstanceUID, not the anon one.
        """
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=1,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid="9.8.7.6.5.4.3.2",
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid=None,  # filtered out by SeriesFilter
        )

        result = mask_record_patient_data(record, user)

        # Study is anonymized
        assert result.study_uid == "9.8.7.6.5.4.3.2"
        assert result.study is not None
        assert result.study.study_uid == "9.8.7.6.5.4.3.2"
        # Orphan series is dropped so the UI cannot open it as a target
        assert result.series is None
        assert result.series_uid is None

    def test_non_anon_study_leaves_series_untouched(self) -> None:
        """Non-anonymized study: series is not touched even if it has anon_uid.

        Shouldn't happen in practice (anonymization creates study.anon_uid
        first), but guard against divergent (original_study, anon_series)
        pairs that would confuse OHIF just like the reverse case.
        """
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            patient_id="REAL_PAT_001",
            patient_name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=1,
            study_uid="1.2.3.4.5.6.7.8",
            study_anon_uid=None,
            series_uid="1.2.3.4.5.6.7.8.9",
            series_anon_uid="9.8.7.6.5.4.3.2.1",
        )

        result = mask_record_patient_data(record, user)

        # Both study and series keep original UIDs — consistent pair
        assert result.study_uid == "1.2.3.4.5.6.7.8"
        assert result.study is not None
        assert result.study.study_uid == "1.2.3.4.5.6.7.8"
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

    def test_record_type_opted_out_keeps_display_anon_id(self) -> None:
        """Opt-out types still carry the per-study display ID next to real data.

        Specialist roles (``mask_patient_data=False``) see real identifiers, but
        the ANON ID column keeps showing the per-study hash so the row can
        still be correlated with the anonymized study in PACS.
        """
        user = _make_user(is_superuser=False)
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id_hex_length = 8
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = "DEMO_PART"
            record = _make_record_read(auto_id=42, mask_patient_data=False)

            result = mask_record_patient_data(record, user)

        expected_hash = hashlib.sha256(b"test-salt:1.2.3.4.5.6.7.8").hexdigest()[:8]
        assert result.patient_id == "REAL_PAT_001"
        assert result.patient.name == "Real Patient Name"
        assert result.display_anon_id == f"DEMO_PART_{expected_hash}"

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

    def test_mask_records_masks_study_date_and_description(self) -> None:
        """Batch mask_records masks study date + description, not just identifiers.

        mask_records is the list-path used by /records/find and the list
        endpoints; it delegates to mask_record_patient_data, so this guards the
        actual user-facing batch surface against the date/description leak.
        """
        user = _make_user(is_superuser=False)
        record = _make_record_read(
            anon_name="Anon Patient Name",
            auto_id=1,
            study_anon_uid="9.8.7.6.5.4.3.2",
            study_date=date(2025, 1, 17),
            study_description="CT SCAN PART - BATCH 002",
            series_uid=None,
            series_anon_uid=None,
        )

        results = mask_records([record], user)  # type: ignore[arg-type, list-item]

        assert len(results) == 1
        assert results[0].study is not None
        assert results[0].study.date == _MASKED_STUDY_DATE
        assert results[0].study.study_description is None

    def test_mask_records_empty_list(self) -> None:
        """mask_records handles empty list correctly."""
        user = _make_user(is_superuser=False)
        results = mask_records([], user)
        assert results == []

    def test_per_study_mode_replaces_patient_id_with_hash(self) -> None:
        """Per-study mode: masked PatientID is sha256(salt:study_uid)[:8], not anon_id."""
        user = _make_user(is_superuser=False)
        # The hash is computed by the RecordRead validator at construction time
        # (masking just reuses display_anon_id), so the record must be built
        # inside the settings patch.
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id_hex_length = 8
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = ""  # bare-hash backward-compat path
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

        expected_hash = hashlib.sha256(b"test-salt:1.2.3.4.5.6.7.8").hexdigest()[:8]
        # Top-level + nested all carry the per-study hash, not f"{prefix}_42"
        assert result.patient_id == expected_hash
        assert result.patient.id == expected_hash
        assert result.patient.name == expected_hash
        assert result.study is not None
        assert result.study.patient_id == expected_hash
        # Study/series UIDs still come from study.anon_uid / series.anon_uid
        assert result.study_uid == "9.8.7.6.5.4.3.2"
        assert result.series is not None
        assert result.series.series_uid == "9.8.7.6.5.4.3.2.1"

    def test_per_study_branch_hashes_raw_study_uid_when_not_yet_anonymized(self) -> None:
        """Covers the per-study masking branch (``display_anon_id`` is None).

        ``mask()`` reads its OWN ``settings`` reference for the per-study gate, so
        the branch is only exercised when ``clarinet.api.masking.settings`` is
        patched (not just ``_storage.settings``). With the study not yet
        anonymized it derives the hash from the RAW ``study_uid`` via
        ``Files.per_study_patient_id``.
        """
        user = _make_user(is_superuser=False)
        with (
            patch("clarinet.api.masking.settings") as mask_settings,
            patch("clarinet.files._storage.settings") as fs_settings,
        ):
            mask_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id_hex_length = 8
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = ""
            record = _make_record_read(
                patient_id="REAL_PAT_001",
                patient_name="Real Patient Name",
                anon_name="Anon Patient Name",
                auto_id=42,
                study_uid="1.2.3.4.5.6.7.8",
                study_anon_uid=None,  # not yet anonymized → display_anon_id is None
                series_uid="1.2.3.4.5.6.7.8.9",
                series_anon_uid=None,
            )
            result = mask_record_patient_data(record, user)

        expected_hash = hashlib.sha256(b"test-salt:1.2.3.4.5.6.7.8").hexdigest()[:8]
        assert result.patient_id == expected_hash
        assert result.patient.id == expected_hash
        assert result.patient.name == expected_hash  # masked_name = masked_id

    def test_per_study_mode_with_anon_id_prefix_prepends_prefix(self) -> None:
        """Per-study mode + non-empty anon_id_prefix: masked PatientID is f'{prefix}_{hash}'."""
        user = _make_user(is_superuser=False)
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id_hex_length = 8
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = "DEMO_PART"
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

        expected_hash = hashlib.sha256(b"test-salt:1.2.3.4.5.6.7.8").hexdigest()[:8]
        expected_id = f"DEMO_PART_{expected_hash}"
        assert result.patient_id == expected_id
        assert result.patient.id == expected_id
        assert result.patient.name == expected_id
        assert result.study is not None
        assert result.study.patient_id == expected_id

    def test_per_study_mode_falls_back_until_study_anonymized(self) -> None:
        """Per-study mode + study.anon_uid=None: fall back to per-patient anon_id.

        Before anonymization completes, PACS still holds the real PatientID, so
        returning the per-study hash here would point at nothing real. Fall back
        to anon_id until study.anon_uid is set.
        """
        user = _make_user(is_superuser=False)
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = ""
            record = _make_record_read(
                patient_id="REAL_PAT_001",
                patient_name="Real Patient Name",
                anon_name="Anon Patient Name",
                auto_id=42,
                study_uid="1.2.3.4.5.6.7.8",
                study_anon_uid=None,  # study not yet anonymized
                series_uid="1.2.3.4.5.6.7.8.9",
                series_anon_uid=None,
            )
            result = mask_record_patient_data(record, user)

        # Hash NOT applied — patient-level anon_id used instead.
        expected_anon_id = f"{settings.anon_id_prefix}_42"
        assert result.patient_id == expected_anon_id
        assert result.patient.id == expected_anon_id
        assert result.patient.name == "Anon Patient Name"

    def test_per_study_mode_falls_back_when_no_study_uid(self) -> None:
        """Per-study mode without study_uid (PATIENT-level): fall back to anon_id."""
        user = _make_user(is_superuser=False)

        patient = PatientInfo(
            id="REAL_PAT_001",
            name="Real Patient Name",
            anon_name="Anon Patient Name",
            auto_id=42,
        )
        record_type = RecordTypeRead(
            name="patient-type",
            level=DicomQueryLevel.PATIENT,
        )
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = ""
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

        # No study_uid -> per-study hash impossible; falls back to per-patient anon_id
        expected_anon_id = f"{settings.anon_id_prefix}_42"
        assert result.patient_id == expected_anon_id
        assert result.patient.id == expected_anon_id
        assert result.patient.name == "Anon Patient Name"

    def test_display_anon_id_none_in_per_patient_mode(self) -> None:
        """Default mode: display_anon_id stays None (consumers use patient.anon_id)."""
        record = _make_record_read(auto_id=42)

        assert record.display_anon_id is None

    def test_display_anon_id_per_study_mode(self) -> None:
        """Per-study mode + anonymized study: display_anon_id is the per-study hash."""
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id_hex_length = 8
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = "DEMO_PART"
            record = _make_record_read(
                auto_id=42,
                study_uid="1.2.3.4.5.6.7.8",
                study_anon_uid="9.8.7.6.5.4.3.2",
            )

        expected_hash = hashlib.sha256(b"test-salt:1.2.3.4.5.6.7.8").hexdigest()[:8]
        assert record.display_anon_id == f"DEMO_PART_{expected_hash}"

    def test_display_anon_id_none_until_study_anonymized(self) -> None:
        """Per-study mode + study.anon_uid=None: display_anon_id stays None."""
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id_hex_length = 8
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = "DEMO_PART"
            record = _make_record_read(auto_id=42, study_anon_uid=None)

        assert record.display_anon_id is None

    def test_display_anon_id_survives_masking_and_revalidation(self) -> None:
        """Masking + FastAPI response re-validation keep the original-UID hash.

        Masking rewrites study_uid to the anon UID; if display_anon_id were
        recomputed after that (FastAPI re-validates response models), the hash
        would be taken from the anon UID and stop matching the PatientID in PACS.
        """
        user = _make_user(is_superuser=False)
        with patch("clarinet.files._storage.settings") as fs_settings:
            fs_settings.anon_per_study_patient_id = True
            fs_settings.anon_per_study_patient_id_hex_length = 8
            fs_settings.anon_uid_salt = "test-salt"
            fs_settings.anon_id_prefix = "DEMO_PART"
            record = _make_record_read(
                auto_id=42,
                study_uid="1.2.3.4.5.6.7.8",
                study_anon_uid="9.8.7.6.5.4.3.2",
            )

            result = mask_record_patient_data(record, user)
            revalidated = RecordRead.model_validate(result.model_dump())

        expected_hash = hashlib.sha256(b"test-salt:1.2.3.4.5.6.7.8").hexdigest()[:8]
        expected_id = f"DEMO_PART_{expected_hash}"
        # display matches the masked PatientID, not a hash of the anon study UID
        assert result.display_anon_id == expected_id
        assert result.patient_id == expected_id
        assert result.study_uid == "9.8.7.6.5.4.3.2"
        assert revalidated.display_anon_id == expected_id

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


class TestPerStudyAnonIdStripping:
    """Per-study mode hides the stable per-patient anon_id / auto_id.

    When ``anon_per_study_patient_id`` is enabled the per-patient ``anon_id``
    (``{prefix}_{auto_id}``) is stable across a patient's studies, so handing it
    to a non-superuser would let them correlate studies the per-study hashing is
    meant to keep unlinkable. The masked response must therefore expose only the
    per-study hash and drop ``anon_id`` / ``auto_id`` from the payload.
    """

    def test_non_superuser_strips_anon_id_and_auto_id(self) -> None:
        """Non-superuser: per-study hash is visible, per-patient ids are gone."""
        user = _make_user(is_superuser=False)
        with patch.object(settings, "anon_per_study_patient_id", True):
            record = _make_record_read(
                patient_id="REAL_PAT_001",
                anon_name="Anon Patient Name",
                auto_id=42,
                study_uid="1.2.3.4.5.6.7.8",
                study_anon_uid="9.8.7.6.5.4.3.2",
            )
            result = mask_record_patient_data(record, user)

            expected_hash = compute_per_study_patient_id(
                settings.anon_uid_salt,
                "1.2.3.4.5.6.7.8",
                settings.anon_per_study_patient_id_hex_length,
                prefix=settings.anon_id_prefix,
            )

        # The per-study hash is the visible identifier...
        assert result.display_anon_id == expected_hash
        assert result.patient_id == expected_hash
        assert result.patient.id == expected_hash
        # ...and the stable per-patient identifiers never reach the client,
        # including the serialized JSON (anon_id is computed from auto_id;
        # anon_name is a stable per-patient value of its own).
        assert result.patient.auto_id is None
        assert result.patient.anon_id is None
        assert result.patient.anon_name is None
        dumped = result.model_dump()
        assert dumped["patient"]["auto_id"] is None
        assert dumped["patient"]["anon_id"] is None
        assert dumped["patient"]["anon_name"] is None

    def test_superuser_keeps_anon_id(self) -> None:
        """Superuser sees full data even in per-study mode — ids are preserved."""
        superuser = _make_user(is_superuser=True)
        with patch.object(settings, "anon_per_study_patient_id", True):
            record = _make_record_read(
                anon_name="Anon Patient Name",
                auto_id=42,
                study_anon_uid="9.8.7.6.5.4.3.2",
            )
            result = mask_record_patient_data(record, superuser)

        assert result.patient.auto_id == 42
        assert result.patient.anon_id == f"{settings.anon_id_prefix}_42"
        assert result.patient.anon_name == "Anon Patient Name"

    def test_default_mode_keeps_anon_id_for_non_superuser(self) -> None:
        """Default (per-patient) mode is unchanged: the per-patient anon_id is
        still the masked identifier and auto_id is preserved."""
        user = _make_user(is_superuser=False)
        with patch.object(settings, "anon_per_study_patient_id", False):
            record = _make_record_read(
                anon_name="Anon Patient Name",
                auto_id=42,
                study_anon_uid="9.8.7.6.5.4.3.2",
            )
            result = mask_record_patient_data(record, user)

        expected_anon_id = f"{settings.anon_id_prefix}_42"
        assert result.display_anon_id is None
        assert result.patient_id == expected_anon_id
        assert result.patient.anon_id == expected_anon_id
        assert result.patient.auto_id == 42

    def test_race_window_uses_per_study_hash_not_anon_id(self) -> None:
        """Race window: per-study mode ON, patient anonymized, study NOT yet
        anonymized (``study_anon_uid=None`` -> ``display_anon_id`` is None).

        The masked identifier must be the per-study hash of the (still-raw)
        study_uid — NOT the per-patient anon_id / anon_name, which are stable
        across studies and would let a non-superuser correlate them. The real
        patient ID must also not survive on the nested study relation.
        """
        user = _make_user(is_superuser=False)
        with patch.object(settings, "anon_per_study_patient_id", True):
            record = _make_record_read(
                patient_id="REAL_PAT_001",
                anon_name="Anon Patient Name",
                auto_id=42,
                study_uid="1.2.3.4.5.6.7.8",
                study_anon_uid=None,  # study not anonymized yet
            )
            result = mask_record_patient_data(record, user)

            expected_hash = compute_per_study_patient_id(
                settings.anon_uid_salt,
                "1.2.3.4.5.6.7.8",
                settings.anon_per_study_patient_id_hex_length,
                prefix=settings.anon_id_prefix,
            )

        plain_anon_id = f"{settings.anon_id_prefix}_42"
        # display_anon_id stays None (study has no anon_uid)...
        assert result.display_anon_id is None
        # ...but the masked patient identifier is the per-study hash, never the
        # cross-study-stable per-patient anon_id / anon_name.
        assert result.patient_id == expected_hash
        assert result.patient.id == expected_hash
        assert result.patient.name == expected_hash
        assert result.patient_id != plain_anon_id
        assert result.patient.anon_id is None
        assert result.patient.auto_id is None
        assert result.patient.anon_name is None
        # The real patient ID must not leak through the nested study relation.
        assert result.study is not None
        assert result.study.patient_id == expected_hash
        # The study itself is not anonymized, so its UID is left as-is.
        assert result.study.study_uid == "1.2.3.4.5.6.7.8"
