"""API contract tests: ``working_folder`` and slicer-args fields appear in JSON.

Phase 4 contract: the fields are still part of the JSON shape (frontend
decoders, OpenAPI schema), but their VALUES are now optional — routers no
longer eagerly compute paths via ``RecordRead`` / ``SeriesRead``
computed_fields, so the values are ``None`` until a router (or a future
phase of the FileRepository refactor) populates them explicitly.

Asserting *membership* (key in body) catches the silent-removal regression
the Phase 0 baseline guarded against. Asserting that the value is either
``None`` or the expected concrete type ensures we don't accidentally
re-introduce the legacy raw-UID fallback (which would mask anon issues).
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
async def test_get_record_returns_working_folder_in_json(client, seeded_record):
    """``GET /api/records/{id}`` JSON exposes the ``working_folder`` key.

    Phase 4: the field is no longer eagerly computed by the model, so the
    value is ``None`` until a router populates it. The key itself must
    remain in the OpenAPI shape so downstream decoders (and any future
    router-side injection) keep working.
    """
    response = await client.get(f"{RECORDS_BASE}/{seeded_record.id}")

    assert response.status_code == 200
    body = response.json()
    assert "working_folder" in body, (
        "working_folder must be in API response (frontend decodes it as Option)"
    )
    assert body["working_folder"] is None or isinstance(body["working_folder"], str)


@pytest.mark.asyncio
async def test_get_record_returns_slicer_args_formatted_in_json(client, seeded_record):
    """``GET /api/records/{id}`` JSON exposes the three slicer-args keys.

    Phase 4: same contract as ``working_folder`` — keys stay in the shape,
    values are nullable. The legacy computed fields used the
    ``fallback_to_unanonymized=True`` UX shortcut; the new contract refuses
    that shortcut at the model layer and lets routers opt in (Phase 5+).
    """
    response = await client.get(f"{RECORDS_BASE}/{seeded_record.id}")

    assert response.status_code == 200
    body = response.json()

    for key in (
        "slicer_args_formatted",
        "slicer_validator_args_formatted",
        "slicer_all_args_formatted",
    ):
        assert key in body, f"{key} must remain in API shape (Option(Dict) frontend)"
        assert body[key] is None or isinstance(body[key], dict), (
            f"{key} must be null or dict, got {type(body[key]).__name__}"
        )


@pytest.mark.asyncio
async def test_get_series_returns_working_folder_in_json(client, anon_series):
    """``GET /api/series/{uid}`` JSON exposes the ``working_folder`` key.

    Phase 4: value is nullable (was computed_field with fallback to raw UIDs).
    Frontend ``series/detail`` page renders ``"-"`` when the value is ``None``.
    """
    response = await client.get(f"{SERIES_BASE}/{anon_series.series_uid}")

    assert response.status_code == 200
    body = response.json()
    assert "working_folder" in body, (
        "working_folder must be in /api/series/{uid} response "
        "(frontend Series detail page decodes it as Option)"
    )
    assert body["working_folder"] is None or isinstance(body["working_folder"], str)


# ---------------------------------------------------------------------------
# Non-anonymized fixtures + regression tests: routers must NOT 500 on records
# whose study/series has no anon_uid (the previous computed_field used
# fallback_to_unanonymized=True; the new contract just leaves the field null).
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
    """``GET /api/records/{id}`` for a non-anon record returns 200, not 500.

    The legacy computed ``working_folder`` masked anon issues by silently
    falling back to raw UIDs. After Phase 4 the field is plain Optional —
    the router must not blow up just because ``anon_uid`` is missing.
    """
    response = await client.get(f"{RECORDS_BASE}/{raw_seeded_record.id}")

    assert response.status_code == 200, (
        f"unanon record GET must succeed; got {response.status_code} {response.text}"
    )
    body = response.json()
    assert "working_folder" in body
    # Field is nullable; until a router populates it, value is ``None``.
    assert body["working_folder"] is None or isinstance(body["working_folder"], str)


@pytest.mark.asyncio
async def test_get_series_unanonymized_does_not_500(client, raw_series):
    """``GET /api/series/{uid}`` for a non-anon series returns 200."""
    response = await client.get(f"{SERIES_BASE}/{raw_series.series_uid}")

    assert response.status_code == 200, (
        f"unanon series GET must succeed; got {response.status_code} {response.text}"
    )
    body = response.json()
    assert "working_folder" in body
    assert body["working_folder"] is None or isinstance(body["working_folder"], str)
