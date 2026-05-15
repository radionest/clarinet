"""API contract tests: ``working_folder`` and slicer-args fields appear in JSON.

Phase 0 safety net for the FileRepository refactor. Once
``RecordRead.working_folder`` / ``SeriesRead.working_folder`` /
``RecordRead.slicer_*_args_formatted`` move out of the models into
``FileRepository``, router responses must continue exposing the same JSON
shape (frontend depends on ``working_folder`` and slicer args). These tests
fail loudly if the wire contract regresses — without them the model-level
removal could silently break the frontend ``Series detail`` page and the
Slicer kwargs surface.
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
    """``GET /api/records/{id}`` JSON contains ``working_folder`` (non-empty str)."""
    response = await client.get(f"{RECORDS_BASE}/{seeded_record.id}")

    assert response.status_code == 200
    body = response.json()
    assert "working_folder" in body, (
        "working_folder must be in API response (frontend depends on it)"
    )
    assert isinstance(body["working_folder"], str)
    assert body["working_folder"], "working_folder must not be empty"


@pytest.mark.asyncio
async def test_get_record_returns_slicer_args_formatted_in_json(client, seeded_record):
    """``GET /api/records/{id}`` JSON returns all three slicer args fields as dicts.

    Stronger than membership check — ``in body`` would still pass if the
    refactor silently serialised the field as ``null``, which is exactly
    the regression Phase 0 must catch. Asserting ``isinstance(dict)`` +
    placeholder substitution (``{working_folder}`` resolved) closes that.
    """
    response = await client.get(f"{RECORDS_BASE}/{seeded_record.id}")

    assert response.status_code == 200
    body = response.json()

    # All three computed slicer-args fields must be non-null dicts in the
    # response — null would indicate the model-level computed field was
    # dropped without an explicit router-side replacement.
    assert isinstance(body["slicer_args_formatted"], dict)
    assert isinstance(body["slicer_validator_args_formatted"], dict)
    assert isinstance(body["slicer_all_args_formatted"], dict)

    # The RecordType fixture sets ``slicer_script_args = {"out":
    # "{working_folder}/result.nrrd"}`` — confirm the {working_folder}
    # placeholder was actually substituted, not passed through verbatim.
    assert "out" in body["slicer_args_formatted"]
    assert body["slicer_args_formatted"]["out"].endswith("/result.nrrd")
    assert "{working_folder}" not in body["slicer_args_formatted"]["out"]

    # slicer_all_args_formatted aggregates all args and always carries
    # working_folder under that key.
    assert "working_folder" in body["slicer_all_args_formatted"]
    assert isinstance(body["slicer_all_args_formatted"]["working_folder"], str)


@pytest.mark.asyncio
async def test_get_series_returns_working_folder_in_json(client, anon_series):
    """``GET /api/series/{uid}`` JSON contains ``working_folder``.

    Frontend ``series/detail`` page reads this field directly. SeriesRead has
    no per-record override — always uses ``settings.storage_path``.
    """
    response = await client.get(f"{SERIES_BASE}/{anon_series.series_uid}")

    assert response.status_code == 200
    body = response.json()
    assert "working_folder" in body, (
        "working_folder must be in /api/series/{uid} response "
        "(frontend Series detail page depends on it)"
    )
    # SeriesRead.working_folder is typed ``str`` (never None) per the model
    # contract, but the Gleam decoder accepts ``Option(String)`` — assert the
    # backend really sends a non-empty string so the refactor doesn't silently
    # downgrade it to ``null``.
    assert isinstance(body["working_folder"], str)
    assert body["working_folder"]


@pytest.mark.asyncio
async def test_series_working_folder_ignores_record_clarinet_storage_path(
    client, test_session, anon_patient, anon_study, anon_series, rt_series_with_slicer_args
):
    """``SeriesRead.working_folder`` is invariant under ``Record.clarinet_storage_path``.

    Asymmetry-by-design (see ``RecordRead._get_working_folder`` docstring):
    per-record override exists only on Record, never on Series — so even
    when a Record points at the same Series with a custom storage root,
    ``GET /api/series/{uid}`` always returns ``settings.storage_path`` based
    path. A future refactor must not "fix" the asymmetry by mistake.
    """
    custom_storage = "/custom/series-asymmetry/root"
    await seed_record(
        test_session,
        patient_id=anon_patient.id,
        study_uid=anon_study.study_uid,
        series_uid=anon_series.series_uid,
        rt_name=rt_series_with_slicer_args.name,
        clarinet_storage_path=custom_storage,
    )

    response = await client.get(f"{SERIES_BASE}/{anon_series.series_uid}")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["working_folder"], str)
    assert custom_storage not in body["working_folder"], (
        "SeriesRead must NOT pick up Record.clarinet_storage_path — intentional asymmetry"
    )
