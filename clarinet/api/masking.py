"""Patient data masking for non-superuser responses.

When a patient has been anonymized (anon_name is set), non-superusers see
anonymized identifiers instead of real patient/study/series data.

RecordTypes may opt out of masking via ``mask_patient_data=False`` — used for
record types filled by clinicians who need real patient IDs (surgery,
pathology, MDK). Each deanonymized access is audit-logged at INFO level
without leaking PII (identifiers only).
"""

from collections.abc import Sequence

from clarinet.models import Record, RecordRead, User
from clarinet.utils.logger import logger


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

    # Build masked patient
    patient_update = {}
    if record.patient.anon_id is not None:
        patient_update["id"] = record.patient.anon_id
    if record.patient.anon_name is not None:
        patient_update["name"] = record.patient.anon_name

    masked_patient = record.patient.model_copy(update=patient_update)

    updates: dict = {"patient": masked_patient}
    if record.patient.anon_id is not None:
        updates["patient_id"] = record.patient.anon_id

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
        study_nested_update: dict = {"study_uid": record.study.anon_uid}
        if record.patient.anon_id is not None:
            study_nested_update["patient_id"] = record.patient.anon_id
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
