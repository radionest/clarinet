"""Integration tests for ``POST /api/records/{id}/submit`` __execResult merging.

Cover the path where ``slicer_result_validator`` returns extra fields in
``__execResult`` and the framework merges them into ``record.data`` before save.

Slicer is mocked via dependency override — these tests do **not** depend on a
running 3D Slicer instance.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from clarinet.api.app import app
from clarinet.api.dependencies import get_slicer_service
from clarinet.exceptions.domain import FieldError, RecordDataValidationError, SlicerError
from clarinet.models.base import DicomQueryLevel
from clarinet.models.record import Record, RecordStatus, RecordType
from clarinet.models.study import Study
from clarinet.services.record_data_validation import record_validator
from clarinet.services.slicer.service import SlicerService
from tests.utils.factories import make_patient
from tests.utils.urls import RECORDS_BASE

_BOUNDS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "x_min": {"type": "number"},
        "x_max": {"type": "number"},
        "y_min": {"type": "number"},
        "y_max": {"type": "number"},
        "z_min": {"type": "number"},
        "z_max": {"type": "number"},
    },
}


@pytest_asyncio.fixture
async def cropping_box_record(test_session) -> Record:
    """Patient + study + RecordType-with-validator + pending Record.

    The validator script is a placeholder — its actual execution is mocked.
    """
    patient = make_patient("EXEC_PAT001", "ExecResult Patient")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.901.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="exec-bounds-test",
        level=DicomQueryLevel.STUDY,
        data_schema=_BOUNDS_SCHEMA,
        slicer_result_validator="__execResult = {}  # stub — mocked in tests",
    )
    test_session.add(rt)
    await test_session.flush()

    rec = Record(
        record_type_name=rt.name,
        patient_id=patient.id,
        study_uid=study.study_uid,
        status=RecordStatus.pending,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)
    return rec


@pytest_asyncio.fixture
async def mock_slicer_service() -> AsyncGenerator[AsyncMock]:
    """Replace ``SlicerService`` in DI with an ``AsyncMock``.

    The ``execute`` coroutine returns an empty dict by default; tests
    override ``mock.execute.return_value`` to drive scenarios.
    """
    mock = AsyncMock(spec=SlicerService)
    mock.execute.return_value = {}

    async def _override() -> SlicerService:
        return mock  # type: ignore[return-value]

    app.dependency_overrides[get_slicer_service] = _override
    try:
        yield mock
    finally:
        app.dependency_overrides.pop(get_slicer_service, None)


def _full_bounds() -> dict[str, float]:
    return {
        "x_min": 0.0,
        "x_max": 1.0,
        "y_min": 0.0,
        "y_max": 1.0,
        "z_min": 0.0,
        "z_max": 1.0,
    }


@pytest.mark.asyncio
async def test_submit_merges_execresult_into_data(
    client, cropping_box_record: Record, mock_slicer_service: AsyncMock
):
    """Validator returns __execResult — keys appear in record.data after save."""
    bounds = _full_bounds()
    mock_slicer_service.execute.return_value = bounds

    resp = await client.post(f"{RECORDS_BASE}/{cropping_box_record.id}/submit", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == bounds
    mock_slicer_service.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_execresult_wins_on_key_conflict(
    client, cropping_box_record: Record, mock_slicer_service: AsyncMock
):
    """On overlapping keys validator value persists, user value is overwritten."""
    mock_slicer_service.execute.return_value = {"x_min": 1.0}

    form = {**_full_bounds(), "x_min": 99.0}
    resp = await client.post(f"{RECORDS_BASE}/{cropping_box_record.id}/submit", json=form)

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["x_min"] == 1.0  # validator value wins
    assert data["x_max"] == 1.0
    assert data["y_min"] == 0.0
    assert data["y_max"] == 1.0
    assert data["z_min"] == 0.0
    assert data["z_max"] == 1.0


@pytest.mark.asyncio
async def test_submit_empty_execresult_leaves_data_unchanged(
    client, cropping_box_record: Record, mock_slicer_service: AsyncMock
):
    """Validator returns {} → record.data equals submitted form, no merge."""
    mock_slicer_service.execute.return_value = {}

    form = _full_bounds()
    resp = await client.post(f"{RECORDS_BASE}/{cropping_box_record.id}/submit", json=form)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == form


@pytest.mark.asyncio
async def test_submit_execresult_revalidates_against_schema(
    client, cropping_box_record: Record, mock_slicer_service: AsyncMock
):
    """Validator returns value that fails schema → 422 (re-validation works)."""
    mock_slicer_service.execute.return_value = {"x_min": "not_a_number"}

    resp = await client.post(f"{RECORDS_BASE}/{cropping_box_record.id}/submit", json=_full_bounds())

    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body.get("detail") == "Validation failed"
    paths = [err.get("path") for err in body.get("errors", [])]
    assert any("x_min" in (p or "") for p in paths), body


@pytest.mark.asyncio
async def test_submit_no_validator_skips_slicer(
    client, test_session, mock_slicer_service: AsyncMock
):
    """RecordType without slicer_result_validator → execute() not called."""
    patient = make_patient("EXEC_PAT002", "ExecResult Patient 2")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.901.2",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="exec-no-validator-test",
        level=DicomQueryLevel.STUDY,
        data_schema=_BOUNDS_SCHEMA,
        # slicer_result_validator intentionally left unset
    )
    test_session.add(rt)
    await test_session.flush()

    rec = Record(
        record_type_name=rt.name,
        patient_id=patient.id,
        study_uid=study.study_uid,
        status=RecordStatus.pending,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)

    resp = await client.post(f"{RECORDS_BASE}/{rec.id}/submit", json=_full_bounds())

    assert resp.status_code == 200, resp.text
    mock_slicer_service.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_resubmit_patch_merges_execresult_into_data(
    client, test_session, mock_slicer_service: AsyncMock
):
    """PATCH /submit (resubmit on finished) also merges __execResult into data."""
    patient = make_patient("EXEC_PAT003", "ExecResult Patient 3")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.901.3",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="exec-resubmit-test",
        level=DicomQueryLevel.STUDY,
        data_schema=_BOUNDS_SCHEMA,
        slicer_result_validator="__execResult = {}  # stub — mocked in tests",
    )
    test_session.add(rt)
    await test_session.flush()

    rec = Record(
        record_type_name=rt.name,
        patient_id=patient.id,
        study_uid=study.study_uid,
        status=RecordStatus.finished,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)

    updated = {**_full_bounds(), "x_min": -1.0}
    mock_slicer_service.execute.return_value = updated

    resp = await client.patch(f"{RECORDS_BASE}/{rec.id}/submit", json={})

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == updated
    mock_slicer_service.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_non_dict_execresult_logged_and_skipped(
    client,
    cropping_box_record: Record,
    mock_slicer_service: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
):
    """Non-dict __execResult (broken contract) → warning + skip merge, no 500.

    Loguru output is not captured by pytest's ``caplog``; patch
    ``logger.warning`` directly (same workaround as
    ``tests/test_record_data_validation.py``).
    """
    from clarinet.services import record_type_service as rts_module

    warnings: list[str] = []
    monkeypatch.setattr(
        rts_module.logger, "warning", lambda msg, *a, **kw: warnings.append(str(msg))
    )

    mock_slicer_service.execute.return_value = ["not", "a", "dict"]

    form = _full_bounds()
    resp = await client.post(f"{RECORDS_BASE}/{cropping_box_record.id}/submit", json=form)

    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == form
    assert any("slicer_validator_result_unexpected_type" in w for w in warnings), warnings
    assert any("list" in w for w in warnings), warnings


@pytest.mark.asyncio
async def test_submit_validator_raises_returns_422(
    client, cropping_box_record: Record, mock_slicer_service: AsyncMock
):
    """SlicerError from validator (raised ValueError, etc.) → 422 on /submit."""
    mock_slicer_service.execute.side_effect = SlicerError(
        "Slicer execution failed: ROI not aligned"
    )

    resp = await client.post(f"{RECORDS_BASE}/{cropping_box_record.id}/submit", json=_full_bounds())

    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_submit_custom_python_validator_sees_merged_keys(
    client,
    test_session,
    mock_slicer_service: AsyncMock,
    isolated_validator_registry,
):
    """Pass-2 custom Python validator observes validator-injected key values.

    Form passes pass-1 (x_min ≥ 0); validator overrides x_min with a negative
    value; pass-2 sees the merged dict and the custom validator rejects.
    Confirms that validator-injected keys flow through ``run_record_validators``
    on the second validation pass, not just JSON Schema.
    """
    seen_x_min: list[float] = []

    @record_validator("test.x_min_nonneg", run_on_partial=False)
    async def x_min_nonneg(record, data, ctx):
        x = data.get("x_min", 0.0)
        seen_x_min.append(x)
        if x < 0:
            raise RecordDataValidationError(
                [FieldError(path="/x_min", message="x_min must be ≥ 0", code="negative")]
            )

    patient = make_patient("EXEC_PAT004", "ExecResult Patient 4")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.901.4",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="exec-custom-validator-test",
        level=DicomQueryLevel.STUDY,
        data_schema=_BOUNDS_SCHEMA,
        data_validators=["test.x_min_nonneg"],
        slicer_result_validator="__execResult = {}  # stub — mocked in tests",
    )
    test_session.add(rt)
    await test_session.flush()

    rec = Record(
        record_type_name=rt.name,
        patient_id=patient.id,
        study_uid=study.study_uid,
        status=RecordStatus.pending,
    )
    test_session.add(rec)
    await test_session.commit()
    await test_session.refresh(rec)

    mock_slicer_service.execute.return_value = {"x_min": -1.0}

    resp = await client.post(f"{RECORDS_BASE}/{rec.id}/submit", json=_full_bounds())

    assert resp.status_code == 422, resp.text
    assert seen_x_min == [0.0, -1.0], seen_x_min
    body = resp.json()
    assert body.get("detail") == "Validation failed"
    paths = [err.get("path") for err in body.get("errors", [])]
    assert "/x_min" in paths, body
