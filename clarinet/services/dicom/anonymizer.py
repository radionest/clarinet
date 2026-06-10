"""DICOM dataset anonymizer with deterministic UID generation.

Uses dicomanonymizer for standard tag handling and provides deterministic
hash-based UIDs for consistent anonymization (no history tracking needed).
"""

import hashlib
from collections.abc import Iterable, Sequence

from dicomanonymizer import simpledicomanonymizer  # type: ignore[import-untyped]
from pydicom import Dataset
from pydicom.datadict import dictionary_VR
from pydicom.multival import MultiValue
from pydicom.sequence import Sequence as DicomSequence
from pydicom.valuerep import VALIDATORS

from clarinet.utils.logger import logger

# Text VRs only: binary VRs are multi-valued without "\\" separators.
_AUDIT_STRING_VRS = frozenset(
    {"AE", "AS", "CS", "DA", "DS", "DT", "IS", "LO", "LT", "PN", "SH", "ST", "TM", "UI", "UR"}
)
# Cap reported values: an over-length LT/ST/UR violation can exceed 10 KiB, and
# full free-text values do not belong in the worker log.
_MAX_SHOWN_VALUE_LEN = 64


def find_invalid_vr_values(datasets: Iterable[Dataset]) -> dict[tuple[str, str, str], int]:
    """Count string-VR values violating DICOM VR constraints (length/charset).

    Returns ``{(tag "GGGG,EEEE", vr, value): n_instances}`` — each dataset is
    counted once per distinct violation, recursing into sequence items (Philips
    enhanced multiframe keeps per-frame tags in functional-group sequences).
    Catches scanner output (e.g. Philips float-formatted IS counters) that
    strict DICOM JSON consumers later reject. Reported values are truncated to
    ``_MAX_SHOWN_VALUE_LEN`` characters.
    """
    findings: dict[tuple[str, str, str], int] = {}
    for ds in datasets:
        violations: set[tuple[str, str, str]] = set()
        _collect_invalid_vr_values(ds, violations)
        for key in violations:
            findings[key] = findings.get(key, 0) + 1
    return findings


def _collect_invalid_vr_values(ds: Dataset, violations: set[tuple[str, str, str]]) -> None:
    for elem in ds.elements():  # yields elements as-is, without forcing conversion
        tag = int(elem.tag)
        vr = getattr(elem, "VR", None)
        if vr in (None, "UN"):
            try:
                vr = dictionary_VR(tag)
            except KeyError:
                continue
        vr = str(vr)
        value = getattr(elem, "value", None)
        if vr == "SQ":
            # Recurse into converted sequence items; raw undecoded SQ bytes are
            # skipped to preserve the no-forced-conversion design.
            if isinstance(value, DicomSequence):
                for item in value:
                    _collect_invalid_vr_values(item, violations)
            continue
        if vr not in _AUDIT_STRING_VRS:
            continue
        validator = VALIDATORS.get(vr)
        if validator is None:
            continue
        value = getattr(value, "original_string", value)
        parts: Sequence[str | bytes]
        if isinstance(value, str | bytes):
            raw = value.rstrip(b"\x00 ") if isinstance(value, bytes) else value
            parts = raw.split(b"\\") if isinstance(raw, bytes) else raw.split("\\")
        elif isinstance(value, MultiValue):
            # Converted multi-value element — the norm at the anonymization call
            # site, where every raw element has been converted by the anonymizer
            # walk. Each item retains its own original_string (IS/DS/PN).
            parts = [
                part
                for part in (getattr(item, "original_string", item) for item in value)
                if isinstance(part, str | bytes)
            ]
        else:
            continue
        for part in parts:
            valid, _msg = validator(vr, part)
            if valid:
                continue
            shown = part.decode(errors="replace") if isinstance(part, bytes) else part
            if len(shown) > _MAX_SHOWN_VALUE_LEN:
                shown = shown[:_MAX_SHOWN_VALUE_LEN] + "..."
            violations.add((f"{tag >> 16:04X},{tag & 0xFFFF:04X}", vr, shown))


def compute_per_study_patient_id(
    salt: str,
    study_uid: str,
    length: int = 8,
    prefix: str | None = None,
) -> str:
    """Per-study deterministic PatientID/PatientName for DICOM anonymization.

    sha256(f"{salt}:{study_uid}") -> first ``length`` hex characters, optionally
    prefixed with ``f"{prefix}_"``. Same study_uid + salt + length + prefix ->
    same result (idempotent re-runs). Used when
    ``settings.anon_per_study_patient_id`` is enabled to prevent PACS-side
    correlation across studies of the same patient. The ``prefix`` (typically
    ``settings.anon_id_prefix``) makes anonymized studies identifiable as
    belonging to the project on a shared PACS.

    Truncation rationale: short hex keeps the visible PatientID readable while
    staying well within DICOM LO (64) and PN (64 per component) limits. By the
    birthday bound, the default 8 hex chars (32 bits) gives collision
    probability ~0.012% at 1k studies, ~1.15% at 10k, and ~50% near 77k. Tune
    via ``settings.anon_per_study_patient_id_hex_length`` for larger projects
    (16 hex = 64 bits drops collision probability to negligible levels at any
    realistic scale).

    Warning: ``prefix`` is part of the deterministic key alongside ``salt``
    and ``length``. Changing ``anon_id_prefix`` after deployment produces
    different PatientIDs for the same source ``study_uid`` — previously
    anonymized studies on the PACS will no longer correlate with new runs.
    Treat the prefix as immutable for the lifetime of an anonymization
    project.

    DICOM LO (64-char) constraint: ``len(prefix) + 1 + length <= 64`` raises
    ``ValueError`` to fail fast on misconfiguration (survives ``python -O``,
    unlike ``assert``). The Settings-level validator already caps
    ``settings.anon_id_prefix`` at 55 chars; this re-check defends callers
    that pass a custom ``length``.

    Args:
        salt: Salt for deterministic hashing.
        study_uid: Study Instance UID.
        length: Hex slice length (default 8).
        prefix: Optional project prefix; if None or empty, only the hash is
            returned (backward-compatible default).

    Returns:
        ``f"{prefix}_{hash}"`` if prefix is set, else just the hash.

    Raises:
        ValueError: when ``len(prefix) + 1 + length`` exceeds the DICOM LO
            64-char limit.
    """
    digest = hashlib.sha256(f"{salt}:{study_uid}".encode()).hexdigest()[:length]
    if prefix:
        total = len(prefix) + 1 + length
        if total > 64:
            raise ValueError(
                f"prefix '{prefix}' ({len(prefix)} chars) + 1 + {length}-hex "
                f"hash = {total} chars exceeds DICOM LO 64-char limit"
            )
        return f"{prefix}_{digest}"
    return digest


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
