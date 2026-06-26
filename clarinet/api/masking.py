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
is a per-study hash derived from the study UID — the same value written into
the DICOM tags in PACS, so the frontend stays consistent with OHIF and PACS.
The hash is used even before the study is anonymized (the race window where
``display_anon_id`` is still None): it is forward-consistent with the eventual
PACS value and, unlike the per-patient ``anon_id`` / ``anon_name``, is
per-study, so it never lets a non-superuser correlate a patient's studies. In
that mode the per-patient ``auto_id`` (and the ``anon_id`` computed from it)
and ``anon_name`` are also dropped from the payload, since all are stable
across studies. Patient-level records (no study) have no per-study context and
keep the per-patient anon_id.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from clarinet.files import Files
from clarinet.models import Record, RecordRead, User
from clarinet.settings import settings
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

    # Choose the masked PatientID / PatientName.
    #
    # - Study already anonymized (``display_anon_id`` set): the RecordRead
    #   validator computed the per-study hash from the original study_uid — the
    #   same value written into the DICOM tags in PACS, keeping the UI
    #   consistent with OHIF/PACS.
    # - Per-study mode but the study is NOT anonymized yet (the race window,
    #   ``display_anon_id`` is None): derive the per-study hash from the
    #   still-raw study_uid directly. It is forward-consistent with the eventual
    #   PACS value and is per-study; falling back to the per-patient anon_id /
    #   anon_name here would expose a cross-study-stable identifier and let a
    #   non-superuser correlate the patient's studies.
    # - Otherwise (per-patient mode, or a patient-level record with no study):
    #   the per-patient anon_id / anon_name is the operative anonymized id.
    if record.display_anon_id is not None:
        masked_id: str | None = record.display_anon_id
        masked_name: str | None = record.display_anon_id
    elif settings.anon_per_study_patient_id and record.study_uid is not None:
        masked_id = Files.per_study_patient_id(record.study_uid)
        masked_name = masked_id
    else:
        masked_id = record.patient.anon_id
        masked_name = record.patient.anon_name

    # Build masked patient
    patient_update: dict[str, Any] = {}
    if masked_id is not None:
        patient_update["id"] = masked_id
    if masked_name is not None:
        patient_update["name"] = masked_name

    # Per-study mode: drop the cross-study-stable per-patient identifiers from
    # the payload. auto_id is the source of the computed anon_id, so nulling it
    # makes both serialize as null; anon_name is likewise stable per patient.
    # Otherwise a non-superuser could read either straight off the wire and
    # correlate the studies even though patient.id/name carry the per-study hash.
    if settings.anon_per_study_patient_id:
        patient_update["auto_id"] = None
        patient_update["anon_name"] = None

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
    elif settings.anon_per_study_patient_id and record.study is not None and masked_id is not None:
        # Race window: the study has no anon_uid yet, so its UIDs / date are
        # left untouched (there is no anonymized counterpart), but the nested
        # study.patient_id would still carry the REAL patient ID. Rewrite it to
        # the same per-study hash so no patient identifier — real or
        # per-patient-anon — leaks through the study relation.
        updates["study"] = record.study.model_copy(update={"patient_id": masked_id})

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
