"""Patient data masking for non-superuser responses.

When a patient has been anonymized (anon_name is set), non-superusers see
anonymized identifiers instead of real patient/study/series data. The study
acquisition date is replaced with a fixed sentinel (1976-01-01) so neither the
exact date nor its year (both quasi-identifiers) leak past anonymization, and
the free-text study description is dropped on the same path.

RecordTypes may opt out of masking via ``mask_patient_data=False`` — used for
record types filled by clinicians who need real patient IDs (surgery,
pathology, MDK). Each deanonymized access is audit-logged at INFO level
without leaking PII (identifiers only).

When ``settings.anon_per_study_patient_id`` is enabled, the masked patient ID
is a per-study hash (matching what was written into the DICOM tags in PACS),
so the frontend stays consistent with OHIF and PACS.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from clarinet.models import Record, RecordRead, User
from clarinet.utils.logger import logger

# Sentinel shown in place of the real study acquisition date for masked
# (anonymized, non-superuser) records — neither the date nor its year leaks.
_MASKED_STUDY_DATE = date(1976, 1, 1)


def mask_record_patient_data(record: RecordRead, user: User) -> RecordRead:
    """Mask patient data in a RecordRead for non-superusers.

    Superusers always see real data. Non-superusers see anonymized identifiers
    when the patient has been anonymized (anon_name is not None).

    Args:
        record: RecordRead to potentially mask.
        user: Current user.

    Returns:
        Original or masked RecordRead.
    """
    if user.is_superuser:
        return record

    if record.patient.anon_name is None:
        return record

    # Per-record-type opt-out: clinical roles (surgeons, pathologists, MDK)
    # need real patient identifiers. Each access is audit-logged.
    if not record.record_type.mask_patient_data:
        logger.info(
            f"deanon_access user_id={user.id} record_id={record.id} "
            f"record_type={record.record_type.name}"
        )
        return record

    # In per-study mode the masked PatientID/PatientName is the hash that was
    # written into the DICOM tags in PACS. The RecordRead validator has already
    # computed it from the ORIGINAL study_uid: display_anon_id is non-None
    # exactly when per-study mode applies and the study has been anonymized
    # (see RecordRead.populate_display_anon_id). Before that, PACS still holds
    # the real PatientID, so fall back to per-patient anon_id.
    if record.display_anon_id is not None:
        masked_id: str | None = record.display_anon_id
        masked_name: str | None = record.display_anon_id
    else:
        masked_id = record.patient.anon_id
        masked_name = record.patient.anon_name

    # Build masked patient
    patient_update = {}
    if masked_id is not None:
        patient_update["id"] = masked_id
    if masked_name is not None:
        patient_update["name"] = masked_name

    masked_patient = record.patient.model_copy(update=patient_update)

    updates: dict[str, Any] = {"patient": masked_patient}
    if masked_id is not None:
        updates["patient_id"] = masked_id

    # Study + series are masked together to keep the (study_uid, series_uid) pair
    # consistent. If study has no anon_uid we leave both untouched — otherwise
    # the UI could end up with an anonymized study pointing at an original
    # series (or vice versa), which breaks OHIF because the series' embedded
    # StudyInstanceUID won't match the URL's StudyInstanceUID.
    study_is_masked = record.study is not None and record.study.anon_uid is not None
    if study_is_masked:
        assert record.study is not None and record.study.anon_uid is not None
        # Also mask nested study.patient_id — otherwise the real patient ID
        # leaks through the study relation even though top-level patient_id
        # is anonymized.
        study_nested_update: dict[str, Any] = {"study_uid": record.study.anon_uid}
        if masked_id is not None:
            study_nested_update["patient_id"] = masked_id
        # Study date + free-text description are quasi-identifiers (PHI) on this
        # non-superuser REST surface: replace the date with a fixed sentinel
        # (neither exact date nor year leaks) and drop the description. This is
        # deliberately stricter than db_scrub (services/db_scrub/scrubber.py),
        # which keeps study.date for ``{study_date}`` path templates on the
        # trusted test stand. Study is the only date-bearing level here —
        # SeriesBase carries no date/time; if a StudyTime or a series date is
        # ever added to the model, mask it in this branch too.
        study_nested_update["date"] = _MASKED_STUDY_DATE
        study_nested_update["study_description"] = None
        updates["study"] = record.study.model_copy(update=study_nested_update)
        updates["study_uid"] = record.study.anon_uid

        if record.series is not None:
            if record.series.anon_uid is not None:
                # Rewrite series.study_uid to the anon study UID too — leaving
                # it as the original would make the nested series still point
                # at a real StudyInstanceUID for non-superusers.
                updates["series"] = record.series.model_copy(
                    update={
                        "series_uid": record.series.anon_uid,
                        "study_uid": record.study.anon_uid,
                    }
                )
                updates["series_uid"] = record.series.anon_uid
            else:
                # Series has no anon_uid while study is anonymized — typically
                # because SeriesFilter excluded it during anonymization (e.g.
                # localizer, low instance count). Leaking the original
                # SeriesInstanceUID under the anon StudyInstanceUID would crash
                # the OHIF HangingProtocolService (study_uid mismatch between
                # URL and series metadata).
                updates["series"] = None
                updates["series_uid"] = None

    return record.model_copy(update=updates)


def mask_records(records: Sequence[Record], user: User) -> list[RecordRead]:
    """Convert Records to RecordRead and apply patient data masking.

    Args:
        records: Sequence of Record ORM objects.
        user: Current user.

    Returns:
        List of masked RecordRead objects.
    """
    return [mask_record_patient_data(RecordRead.model_validate(r), user) for r in records]
