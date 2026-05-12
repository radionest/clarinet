"""Integration tests for custom Python validators on POST/PATCH record-data endpoints.

Verifies the end-to-end path:
  request → router → record_type_service.validate_record_data
        → run_record_validators → RecordDataValidationError
        → exception handler → 422 with structured ``errors`` payload.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from clarinet.exceptions.domain import FieldError, RecordDataValidationError
from clarinet.models.record import Record, RecordStatus, RecordType
from clarinet.models.study import Study
from clarinet.services.record_data_validation import record_validator
from tests.utils.factories import make_patient
from tests.utils.urls import RECORDS_BASE


@pytest.fixture(autouse=True)
def _clean_validator_registry(isolated_validator_registry):
    """Apply ``isolated_validator_registry`` to every test in this file."""


@pytest_asyncio.fixture
async def record_with_failing_validator(test_session):
    """Patient + study + RecordType with a Python validator that always fails."""

    @record_validator("test.always_fails", run_on_partial=False)
    async def always_fails(record, data, ctx):
        raise RecordDataValidationError(
            [
                FieldError(
                    path="/mappings/0/new_id",
                    message="Duplicate value 3",
                    code="duplicate",
                    params={"value": 3, "first_seen": 0},
                )
            ]
        )

    patient = make_patient("VAL_PAT001", "Validator Patient")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="validator-fail-test",
        level="STUDY",
        data_schema={"type": "object"},
        data_validators=["test.always_fails"],
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
async def record_with_passing_validator(test_session):
    """RecordType bound to a validator that always succeeds."""
    called: list[str] = []

    @record_validator("test.always_passes", run_on_partial=False)
    async def always_passes(record, data, ctx):
        called.append("ok")

    patient = make_patient("VAL_PAT002", "Validator Patient 2")
    test_session.add(patient)
    await test_session.flush()

    study = Study(
        patient_id=patient.id,
        study_uid="2.16.840.1.999.2",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.flush()

    rt = RecordType(
        name="validator-pass-test",
        level="STUDY",
        data_schema={"type": "object"},
        data_validators=["test.always_passes"],
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
    return rec, called


@pytest.mark.asyncio
async def test_submit_with_failing_validator_returns_structured_422(
    client, record_with_failing_validator
):
    """POST /data with a failing custom validator → 422 with errors[]."""
    resp = await client.post(
        f"{RECORDS_BASE}/{record_with_failing_validator.id}/data",
        json={"foo": "bar"},
    )
    assert resp.status_code == 422

    body = resp.json()
    assert body["detail"] == "Validation failed"
    assert isinstance(body["errors"], list)
    assert len(body["errors"]) == 1

    err = body["errors"][0]
    assert err["path"] == "/mappings/0/new_id"
    assert err["message"] == "Duplicate value 3"
    assert err["code"] == "duplicate"
    assert err["params"] == {"value": 3, "first_seen": 0}


@pytest.mark.asyncio
async def test_submit_passes_when_validator_succeeds(client, record_with_passing_validator):
    """POST /data with a successful validator → 200 and validator is invoked."""
    record, called = record_with_passing_validator
    resp = await client.post(
        f"{RECORDS_BASE}/{record.id}/data",
        json={"foo": "bar"},
    )
    assert resp.status_code == 200
    assert called == ["ok"]


@pytest.mark.asyncio
async def test_prefill_skips_default_non_partial_validator(client, record_with_failing_validator):
    """PUT /data/prefill should NOT invoke run_on_partial=False validators.

    The failing validator from the fixture has default ``run_on_partial=False``.
    Prefill must succeed (200) — validator is skipped, partial data accepted.
    """
    resp = await client.put(
        f"{RECORDS_BASE}/{record_with_failing_validator.id}/data/prefill",
        json={"foo": "bar"},
    )
    assert resp.status_code == 200
