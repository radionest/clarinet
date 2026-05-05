"""DICOM dataset anonymizer with deterministic UID generation.

Uses dicomanonymizer for standard tag handling and provides deterministic
hash-based UIDs for consistent anonymization (no history tracking needed).
"""

import hashlib

from dicomanonymizer import simpledicomanonymizer  # type: ignore[import-untyped]
from pydicom import Dataset

from clarinet.utils.logger import logger


def compute_per_study_patient_id(salt: str, study_uid: str) -> str:
    """Per-study deterministic PatientID/PatientName for DICOM anonymization.

    sha256(f"{salt}:{study_uid}") -> first 8 hex characters. Same study_uid +
    salt -> same hash (idempotent re-runs). Used when
    ``settings.anon_per_study_patient_id`` is enabled to prevent PACS-side
    correlation across studies of the same patient.

    Truncation rationale (8 hex chars = 32 bits): keeps the visible PatientID
    short and readable while staying well within DICOM LO (64) and PN (64 per
    component) limits. By the birthday bound, collision probability is
    ~0.012% at 1k studies, ~1.15% at 10k, and ~50% near 77k. For projects
    that may exceed ~10k anonymized studies, increase the slice (16 hex =
    64 bits drops collision probability to negligible levels at any realistic
    scale).
    """
    return hashlib.sha256(f"{salt}:{study_uid}".encode()).hexdigest()[:8]


class DicomAnonymizer:
    """Stateless DICOM anonymizer with deterministic UID generation.

    Works entirely in-memory on pydicom Dataset objects. UIDs are generated
    deterministically from a salt + original UID, so re-processing the same
    data always produces identical output.

    Args:
        salt: Salt for deterministic UID hashing
        anon_patient_id: Anonymized patient ID to set
        anon_patient_name: Anonymized patient name to set
    """

    def __init__(self, salt: str, anon_patient_id: str, anon_patient_name: str):
        self.salt = salt
        self.anon_patient_id = anon_patient_id
        self.anon_patient_name = anon_patient_name

    def generate_anon_uid(self, original_uid: str) -> str:
        """Generate a deterministic DICOM UID from original UID.

        Uses SHA-256 of ``{salt}:{original_uid}`` and formats the first
        16 bytes as ``2.25.{integer}``, producing a valid DICOM UID (~44 chars).

        Args:
            original_uid: Original DICOM UID

        Returns:
            Deterministic anonymized UID
        """
        digest = hashlib.sha256(f"{self.salt}:{original_uid}".encode()).digest()
        uid_int = int.from_bytes(digest[:16], byteorder="big")
        return f"2.25.{uid_int}"

    def anonymize_dataset(self, dataset: Dataset) -> None:
        """Anonymize a DICOM dataset in-place.

        Applies dicomanonymizer defaults for most tags (dates, strings,
        private tags) and overrides patient/UID tags with deterministic values.

        Args:
            dataset: pydicom Dataset to anonymize (modified in-place)
        """
        # Clear global UID dictionary to avoid cross-dataset state leaks
        simpledicomanonymizer.dictionary.clear()

        anon_patient_id = self.anon_patient_id
        anon_patient_name = self.anon_patient_name

        original_study_uid = str(getattr(dataset, "StudyInstanceUID", ""))
        original_series_uid = str(getattr(dataset, "SeriesInstanceUID", ""))
        original_sop_uid = str(getattr(dataset, "SOPInstanceUID", ""))

        anon_study_uid = self.generate_anon_uid(original_study_uid) if original_study_uid else ""
        anon_series_uid = self.generate_anon_uid(original_series_uid) if original_series_uid else ""
        anon_sop_uid = self.generate_anon_uid(original_sop_uid) if original_sop_uid else ""

        def _set_patient_id(dataset: Dataset, tag: tuple[int, int]) -> None:
            dataset[tag].value = anon_patient_id

        def _set_patient_name(dataset: Dataset, tag: tuple[int, int]) -> None:
            dataset[tag].value = anon_patient_name

        def _set_study_uid(dataset: Dataset, tag: tuple[int, int]) -> None:
            dataset[tag].value = anon_study_uid

        def _set_series_uid(dataset: Dataset, tag: tuple[int, int]) -> None:
            dataset[tag].value = anon_series_uid

        def _set_sop_uid(dataset: Dataset, tag: tuple[int, int]) -> None:
            dataset[tag].value = anon_sop_uid

        def _preserve_value(dataset: Dataset, tag: tuple[int, int]) -> None:
            pass

        extra_rules: dict[tuple[int, int], object] = {
            (0x0010, 0x0020): _set_patient_id,  # PatientID
            (0x0010, 0x0010): _set_patient_name,  # PatientName
            (0x0020, 0x000D): _set_study_uid,  # StudyInstanceUID
            (0x0020, 0x000E): _set_series_uid,  # SeriesInstanceUID
            (0x0008, 0x0018): _set_sop_uid,  # SOPInstanceUID
            (0x0002, 0x0003): _set_sop_uid,  # MediaStorageSOPInstanceUID
            (0x0008, 0x103E): _preserve_value,  # SeriesDescription — not PHI
        }

        # Remove private tags before walk() to avoid BytesLengthException
        # on malformed vendor-specific tags (e.g. Philips implicit VR)
        dataset.remove_private_tags()

        simpledicomanonymizer.anonymize_dataset(
            dataset,
            extra_anonymization_rules=extra_rules,
            delete_private_tags=False,
        )

        logger.debug(
            f"Anonymized dataset: PatientID={anon_patient_id}, "
            f"SOPInstanceUID={anon_sop_uid[:20]}..."
        )
