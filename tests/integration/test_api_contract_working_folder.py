"""API contract tests: record/series GET endpoints stay healthy.

The legacy ``working_folder`` / ``slicer_*_args_formatted`` JSON keys
were removed from ``RecordRead`` / ``SeriesRead`` together with the
helper methods (FileRepository refactor — Phase 3). The frontend
already decodes these as ``Option(...)`` with ``decode.optional_field``
which tolerates an absent key by returning ``None``.

These tests guard against two specific regressions:

1. The fields are gone from the response shape (catches silent re-
   introduction of the old computed_field plumbing).
2. Non-anonymized records do NOT 500 from the GET endpoints — the old
   computed fields had a ``fallback_to_unanonymized=True`` UX shortcut
   that masked anon issues. After Phase 3 the model is a dumb data
   container and the router stays responsive.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from clarinet.models.base import DicomQueryLevel
from clarinet.models.patient import Patient
from clarinet.models.record import RecordType
from clarinet.models.study import Series, Study
from tests.utils.factories import seed_record
from tests.utils.urls import RECORDS_BASE, SERIES_BASE


@pytest_asyncio.fixture
async def anon_patient(test_session):
    patient = Patient(
        id="API_CONTRACT_PAT",
        name="Contract Patient",
        anon_name="ANON_CONTRACT",
        auto_id=4242,
    )
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def anon_study(test_session, anon_patient):
    study = Study(
        patient_id=anon_patient.id,
        study_uid="1.2.999.840.111",
        date=datetime.now(UTC).date(),
        anon_uid="ANON_STUDY_CONTRACT",
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)
    return study


@pytest_asyncio.fixture
async def anon_series(test_session, anon_study):
    series = Series(
        study_uid=anon_study.study_uid,
        series_uid="1.2.999.840.111.1",
        series_number=1,
        series_description="Contract Series",
        anon_uid="ANON_SERIES_CONTRACT",
    )
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)
    return series


@pytest_asyncio.fixture
async def rt_series_with_slicer_args(test_session):
    rt = RecordType(
        name="api-contract-rt",
        description="Contract test RT",
        label="Contract RT",
        level=DicomQueryLevel.SERIES,
        slicer_script_args={"out": "{working_folder}/result.nrrd"},
        slicer_result_validator_args={"check": "{working_folder}/check.json"},
    )
    test_session.add(rt)
    await test_session.commit()
    await test_session.refresh(rt)
    return rt


@pytest_asyncio.fixture
async def seeded_record(
    test_session, anon_patient, anon_study, anon_series, rt_series_with_slicer_args
):
    return await seed_record(
        test_session,
        patient_id=anon_patient.id,
        study_uid=anon_study.study_uid,
        series_uid=anon_series.series_uid,
        rt_name=rt_series_with_slicer_args.name,
    )


@pytest.mark.asyncio
async def test_get_record_omits_working_folder_and_slicer_args(client, seeded_record):
    """``GET /api/records/{id}`` no longer carries path-resolution keys."""
    response = await client.get(f"{RECORDS_BASE}/{seeded_record.id}")

    assert response.status_code == 200
    body = response.json()
    for removed_key in (
        "working_folder",
        "slicer_args_formatted",
        "slicer_validator_args_formatted",
        "slicer_all_args_formatted",
    ):
        assert removed_key not in body, (
            f"{removed_key} must not appear in the record response — it was "
            "removed together with the model helpers in Phase 3."
        )


@pytest.mark.asyncio
async def test_get_series_omits_working_folder(client, anon_series):
    """``GET /api/series/{uid}`` no longer carries ``working_folder``."""
    response = await client.get(f"{SERIES_BASE}/{anon_series.series_uid}")

    assert response.status_code == 200
    body = response.json()
    assert "working_folder" not in body, (
        "working_folder must not appear in the series response — it was "
        "removed together with the model helpers in Phase 3."
    )


# ---------------------------------------------------------------------------
# Non-anonymized fixtures + regression tests: routers must NOT 500 on records
# whose study/series has no anon_uid (the previous computed_field used
# fallback_to_unanonymized=True; the new model has no path logic at all, so
# the GET endpoint stays healthy by construction).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def raw_patient(test_session):
    """Patient with no anonymization (anon_name = None)."""
    patient = Patient(
        id="API_CONTRACT_RAW_PAT",
        name="Raw Patient",
        anon_name=None,
        auto_id=4243,
    )
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def raw_study(test_session, raw_patient):
    """Study with no anon_uid."""
    study = Study(
        patient_id=raw_patient.id,
        study_uid="1.2.999.840.222",
        date=datetime.now(UTC).date(),
        anon_uid=None,
    )
    test_session.add(study)
    await test_session.commit()
    await test_session.refresh(study)
    return study


@pytest_asyncio.fixture
async def raw_series(test_session, raw_study):
    """Series with no anon_uid."""
    series = Series(
        study_uid=raw_study.study_uid,
        series_uid="1.2.999.840.222.1",
        series_number=1,
        series_description="Raw Series",
        anon_uid=None,
    )
    test_session.add(series)
    await test_session.commit()
    await test_session.refresh(series)
    return series


@pytest_asyncio.fixture
async def raw_seeded_record(
    test_session, raw_patient, raw_study, raw_series, rt_series_with_slicer_args
):
    return await seed_record(
        test_session,
        patient_id=raw_patient.id,
        study_uid=raw_study.study_uid,
        series_uid=raw_series.series_uid,
        rt_name=rt_series_with_slicer_args.name,
    )


@pytest.mark.asyncio
async def test_get_record_unanonymized_does_not_500(client, raw_seeded_record):
    """``GET /api/records/{id}`` for a non-anon record returns 200, not 500."""
    response = await client.get(f"{RECORDS_BASE}/{raw_seeded_record.id}")

    assert response.status_code == 200, (
        f"unanon record GET must succeed; got {response.status_code} {response.text}"
    )


@pytest.mark.asyncio
async def test_get_series_unanonymized_does_not_500(client, raw_series):
    """``GET /api/series/{uid}`` for a non-anon series returns 200."""
    response = await client.get(f"{SERIES_BASE}/{raw_series.series_uid}")

    assert response.status_code == 200, (
        f"unanon series GET must succeed; got {response.status_code} {response.text}"
    )
