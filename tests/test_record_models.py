"""Unit tests for RecordCreate Pydantic validation."""

from clarinet.models.record import RecordCreate


class TestRecordCreatePatientLevel:
    """RecordCreate must accept PATIENT-level records without study_uid/series_uid.

    Regression: study_uid was declared `DicomUID | None` without a default,
    making it required in Pydantic v2 and breaking PATIENT-level record
    creation through ClarinetClient (e.g. mdk-conclusion pipeline task).
    """

    def test_patient_level_without_uids(self):
        """RecordCreate(record_type_name, patient_id) — no study_uid/series_uid."""
        record = RecordCreate(
            record_type_name="mdk-conclusion",
            patient_id="patient-001",
        )
        assert record.study_uid is None
        assert record.series_uid is None
        assert record.patient_id == "patient-001"
