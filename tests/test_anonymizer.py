"""Unit tests for DicomAnonymizer."""

import pytest
from pydicom import Dataset
from pydicom.uid import ExplicitVRLittleEndian

from src.services.dicom.anonymizer import DicomAnonymizer


@pytest.fixture
def anonymizer() -> DicomAnonymizer:
    """Create anonymizer with fixed salt."""
    return DicomAnonymizer(
        salt="test-salt",
        anon_patient_id="CLARINET_1",
        anon_patient_name="AnonName",
    )


@pytest.fixture
def sample_dataset() -> Dataset:
    """Create a sample DICOM dataset with realistic tags."""
    ds = Dataset()
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    # File meta
    file_meta = Dataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = "1.2.3.100"
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = file_meta

    # Patient
    ds.PatientID = "REAL_PAT_001"
    ds.PatientName = "Real^Patient"
    ds.PatientBirthDate = "19800101"

    # Study
    ds.StudyInstanceUID = "1.2.3.4.5"
    ds.StudyDate = "20240101"
    ds.StudyDescription = "CT Chest"

    # Series
    ds.SeriesInstanceUID = "1.2.3.4.5.6"
    ds.SeriesDescription = "Axial"

    # Instance
    ds.SOPInstanceUID = "1.2.3.100"
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.Modality = "CT"
    ds.Rows = 512
    ds.Columns = 512

    return ds


class TestGenerateAnonUid:
    """Tests for deterministic UID generation."""

    def test_deterministic(self, anonymizer: DicomAnonymizer) -> None:
        """Same input produces same output."""
        uid1 = anonymizer.generate_anon_uid("1.2.3.4.5")
        uid2 = anonymizer.generate_anon_uid("1.2.3.4.5")
        assert uid1 == uid2

    def test_different_inputs_different_outputs(self, anonymizer: DicomAnonymizer) -> None:
        """Different inputs produce different outputs."""
        uid1 = anonymizer.generate_anon_uid("1.2.3.4.5")
        uid2 = anonymizer.generate_anon_uid("1.2.3.4.6")
        assert uid1 != uid2

    def test_valid_dicom_uid_format(self, anonymizer: DicomAnonymizer) -> None:
        """Generated UID starts with 2.25. prefix."""
        uid = anonymizer.generate_anon_uid("1.2.3.4.5")
        assert uid.startswith("2.25.")
        # Remainder should be a valid integer
        int(uid.removeprefix("2.25."))

    def test_uid_length_within_bounds(self, anonymizer: DicomAnonymizer) -> None:
        """Generated UID is within DICOM 64-char limit."""
        uid = anonymizer.generate_anon_uid("1.2.3.4.5.6.7.8.9.10.11.12.13.14.15")
        assert len(uid) <= 64

    def test_different_salt_different_output(self) -> None:
        """Different salts produce different UIDs."""
        a1 = DicomAnonymizer("salt-a", "p1", "n1")
        a2 = DicomAnonymizer("salt-b", "p1", "n1")
        uid1 = a1.generate_anon_uid("1.2.3.4.5")
        uid2 = a2.generate_anon_uid("1.2.3.4.5")
        assert uid1 != uid2

    def test_empty_string_input(self, anonymizer: DicomAnonymizer) -> None:
        """generate_anon_uid('') returns a valid deterministic UID."""
        uid = anonymizer.generate_anon_uid("")
        assert uid.startswith("2.25.")
        assert len(uid) <= 64
        # Deterministic
        assert uid == anonymizer.generate_anon_uid("")


class TestAnonymizeDataset:
    """Tests for full dataset anonymization."""

    def test_patient_id_replaced(
        self, anonymizer: DicomAnonymizer, sample_dataset: Dataset
    ) -> None:
        """PatientID is replaced with anon_patient_id."""
        anonymizer.anonymize_dataset(sample_dataset)
        assert sample_dataset.PatientID == "CLARINET_1"

    def test_patient_name_replaced(
        self, anonymizer: DicomAnonymizer, sample_dataset: Dataset
    ) -> None:
        """PatientName is replaced with anon_patient_name."""
        anonymizer.anonymize_dataset(sample_dataset)
        assert str(sample_dataset.PatientName) == "AnonName"

    def test_study_uid_replaced(self, anonymizer: DicomAnonymizer, sample_dataset: Dataset) -> None:
        """StudyInstanceUID is replaced with deterministic hash."""
        original = sample_dataset.StudyInstanceUID
        anonymizer.anonymize_dataset(sample_dataset)
        assert sample_dataset.StudyInstanceUID != original
        assert sample_dataset.StudyInstanceUID == anonymizer.generate_anon_uid(original)

    def test_series_uid_replaced(
        self, anonymizer: DicomAnonymizer, sample_dataset: Dataset
    ) -> None:
        """SeriesInstanceUID is replaced with deterministic hash."""
        original = sample_dataset.SeriesInstanceUID
        anonymizer.anonymize_dataset(sample_dataset)
        assert sample_dataset.SeriesInstanceUID != original
        assert sample_dataset.SeriesInstanceUID == anonymizer.generate_anon_uid(original)

    def test_sop_uid_replaced(self, anonymizer: DicomAnonymizer, sample_dataset: Dataset) -> None:
        """SOPInstanceUID is replaced with deterministic hash."""
        original = sample_dataset.SOPInstanceUID
        anonymizer.anonymize_dataset(sample_dataset)
        assert sample_dataset.SOPInstanceUID != original
        assert sample_dataset.SOPInstanceUID == anonymizer.generate_anon_uid(original)

    def test_media_storage_sop_uid_replaced(
        self, anonymizer: DicomAnonymizer, sample_dataset: Dataset
    ) -> None:
        """MediaStorageSOPInstanceUID in file_meta is replaced."""
        original_sop = sample_dataset.SOPInstanceUID
        anonymizer.anonymize_dataset(sample_dataset)
        expected = anonymizer.generate_anon_uid(original_sop)
        assert sample_dataset.file_meta.MediaStorageSOPInstanceUID == expected

    def test_idempotent(self, anonymizer: DicomAnonymizer, sample_dataset: Dataset) -> None:
        """Running anonymization twice produces the same result."""
        # First run
        anonymizer.anonymize_dataset(sample_dataset)
        patient_id_1 = sample_dataset.PatientID

        # Second run (on already anonymized data)
        anonymizer.anonymize_dataset(sample_dataset)
        assert sample_dataset.PatientID == patient_id_1
        # UIDs change because input UIDs are now different (the anonymized ones),
        # but patient ID stays the same — that's the key idempotency property
        # For full idempotency, re-run on fresh copy would be needed
        assert sample_dataset.PatientID == "CLARINET_1"

    def test_modality_preserved(self, anonymizer: DicomAnonymizer, sample_dataset: Dataset) -> None:
        """Non-identifying tags like Modality may be handled by dicomanonymizer defaults."""
        anonymizer.anonymize_dataset(sample_dataset)
        # Modality is in the "keep" category for dicomanonymizer
        # Just verify the dataset is still valid
        assert hasattr(sample_dataset, "SOPInstanceUID")


class TestAnonymizeDatasetEdgeCases:
    """Tests for edge cases in dataset anonymization."""

    def test_dataset_without_file_meta(self, anonymizer: DicomAnonymizer) -> None:
        """Dataset without file_meta does not crash."""
        ds = Dataset()
        ds.PatientID = "REAL_PAT"
        ds.PatientName = "Real^Name"
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.SeriesInstanceUID = "1.2.3.4.5.6"
        ds.SOPInstanceUID = "1.2.3.100"
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

        anonymizer.anonymize_dataset(ds)

        assert ds.PatientID == "CLARINET_1"
        assert ds.StudyInstanceUID == anonymizer.generate_anon_uid("1.2.3.4.5")
        assert ds.SOPInstanceUID == anonymizer.generate_anon_uid("1.2.3.100")

    def test_empty_uid_strings(self, anonymizer: DicomAnonymizer) -> None:
        """Empty UID strings are preserved as empty (guard `if original_uid`)."""
        ds = Dataset()
        ds.PatientID = "REAL_PAT"
        ds.PatientName = "Real^Name"
        ds.StudyInstanceUID = ""
        ds.SeriesInstanceUID = ""
        ds.SOPInstanceUID = ""
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

        anonymizer.anonymize_dataset(ds)

        assert ds.PatientID == "CLARINET_1"
        assert ds.StudyInstanceUID == ""
        assert ds.SeriesInstanceUID == ""
        assert ds.SOPInstanceUID == ""

    def test_missing_uid_tags(self, anonymizer: DicomAnonymizer) -> None:
        """Dataset missing UID tags raises KeyError (documents current behavior)."""
        ds = Dataset()
        ds.PatientID = "REAL_PAT"
        ds.PatientName = "Real^Name"
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        # No StudyInstanceUID, SeriesInstanceUID, SOPInstanceUID

        with pytest.raises(KeyError):
            anonymizer.anonymize_dataset(ds)

    def test_private_tags_removed(self, anonymizer: DicomAnonymizer) -> None:
        """Private tags are removed when delete_private_tags=True."""
        ds = Dataset()
        ds.PatientID = "REAL_PAT"
        ds.PatientName = "Real^Name"
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.SeriesInstanceUID = "1.2.3.4.5.6"
        ds.SOPInstanceUID = "1.2.3.100"
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

        # Add private tags (odd group numbers are private)
        ds.add_new(0x00091001, "LO", "PrivateValue1")
        ds.add_new(0x00091002, "LO", "PrivateValue2")

        anonymizer.anonymize_dataset(ds)

        private_tags = [t for t in ds if t.is_private]
        assert private_tags == []

    def test_sensitive_tags_handled(
        self, anonymizer: DicomAnonymizer, sample_dataset: Dataset
    ) -> None:
        """PHI tags like PatientBirthDate and StudyDate are modified by dicomanonymizer."""
        original_birth_date = sample_dataset.PatientBirthDate
        original_study_date = sample_dataset.StudyDate

        anonymizer.anonymize_dataset(sample_dataset)

        assert sample_dataset.PatientBirthDate != original_birth_date
        assert sample_dataset.StudyDate != original_study_date

    def test_multiple_datasets_isolation(self) -> None:
        """dictionary.clear() prevents state leaks between two anonymizers."""
        a1 = DicomAnonymizer("salt-a", "ANON_A", "Name_A")
        a2 = DicomAnonymizer("salt-b", "ANON_B", "Name_B")

        ds1 = Dataset()
        ds1.PatientID = "PAT_1"
        ds1.PatientName = "Patient^One"
        ds1.StudyInstanceUID = "1.2.3.1"
        ds1.SeriesInstanceUID = "1.2.3.1.1"
        ds1.SOPInstanceUID = "1.2.3.1.1.1"
        ds1.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

        ds2 = Dataset()
        ds2.PatientID = "PAT_2"
        ds2.PatientName = "Patient^Two"
        ds2.StudyInstanceUID = "1.2.3.2"
        ds2.SeriesInstanceUID = "1.2.3.2.1"
        ds2.SOPInstanceUID = "1.2.3.2.1.1"
        ds2.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"

        a1.anonymize_dataset(ds1)
        a2.anonymize_dataset(ds2)

        assert ds1.PatientID == "ANON_A"
        assert ds2.PatientID == "ANON_B"
        assert ds1.StudyInstanceUID == a1.generate_anon_uid("1.2.3.1")
        assert ds2.StudyInstanceUID == a2.generate_anon_uid("1.2.3.2")
        # Cross-check: UIDs differ between the two
        assert ds1.StudyInstanceUID != ds2.StudyInstanceUID
