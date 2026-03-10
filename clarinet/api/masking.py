"""Patient data masking for non-superuser responses.

When a patient has been anonymized (anon_name is set), non-superusers see
anonymized identifiers instead of real patient/study/series data.
"""

from collections.abc import Sequence

from clarinet.models import Record, RecordRead, User


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

    # Build masked patient
    patient_update = {}
    if record.patient.anon_id is not None:
        patient_update["id"] = record.patient.anon_id
    if record.patient.anon_name is not None:
        patient_update["name"] = record.patient.anon_name

    masked_patient = record.patient.model_copy(update=patient_update)

    # Build masked study
    masked_study = record.study
    if record.study is not None and record.study.anon_uid is not None:
        masked_study = record.study.model_copy(update={"study_uid": record.study.anon_uid})

    # Build masked series
    masked_series = record.series
    if record.series is not None and record.series.anon_uid is not None:
        masked_series = record.series.model_copy(update={"series_uid": record.series.anon_uid})

    # Build top-level updates
    updates: dict = {
        "patient": masked_patient,
        "study": masked_study,
        "series": masked_series,
    }

    # Mask top-level patient_id
    if record.patient.anon_id is not None:
        updates["patient_id"] = record.patient.anon_id

    # Mask top-level study_uid
    if record.study is not None and record.study.anon_uid is not None:
        updates["study_uid"] = record.study.anon_uid

    # Mask top-level series_uid
    if record.series is not None and record.series.anon_uid is not None:
        updates["series_uid"] = record.series.anon_uid

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
