"""Integration tests for the 409 conflict contract on duplicate patient.

Verifies the structured response shape ``{detail, code, metadata?}`` used
by the frontend to render a localized "patient already exists" toast.
"""

import pytest

from clarinet.exceptions.domain import (
    EntityAlreadyExistsError,
    PatientAlreadyExistsError,
)
from tests.utils.factories import make_patient
from tests.utils.urls import PATIENTS_BASE, STUDIES_BASE


@pytest.mark.asyncio
async def test_create_duplicate_patient_returns_structured_409(client, test_session):
    """409 body carries detail + code=PATIENT_ALREADY_EXISTS + patient_name in metadata."""
    existing = make_patient("DUP_PAT001", "Иванов Иван Иванович")
    test_session.add(existing)
    await test_session.commit()

    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "DUP_PAT001", "patient_name": "Whatever"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"] == "Patient with ID 'DUP_PAT001' already exists"
    assert body["code"] == "PATIENT_ALREADY_EXISTS"
    assert body["metadata"] == {
        "patient_id": "DUP_PAT001",
        "patient_name": "Иванов Иван Иванович",
    }


@pytest.mark.asyncio
async def test_duplicate_study_returns_generic_entity_code(client, test_session):
    """Subclasses without overrides fall back to ENTITY_ALREADY_EXISTS, no metadata."""
    from datetime import UTC, datetime

    from clarinet.models.study import Study

    patient = make_patient("DUP_PAT_STUDY", "Patient For Study")
    test_session.add(patient)
    await test_session.commit()

    study = Study(
        patient_id=patient.id,
        study_uid="1.2.3.4.5.999.1",
        date=datetime.now(UTC).date(),
    )
    test_session.add(study)
    await test_session.commit()

    response = await client.post(
        STUDIES_BASE,
        json={
            "study_uid": "1.2.3.4.5.999.1",
            "patient_id": "DUP_PAT_STUDY",
            "date": "2026-01-01",
        },
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "ENTITY_ALREADY_EXISTS"
    assert "metadata" not in body


def test_patient_already_exists_str_excludes_pii():
    """PII guard: str(exc) must NOT carry patient_name (it lands in logs)."""
    exc = PatientAlreadyExistsError(
        patient_id="X",
        patient_name="Иванов Иван Иванович",
    )
    assert "Иванов" not in str(exc)
    assert "X" in str(exc)


def test_patient_already_exists_metadata_carries_name():
    """patient_name travels only via metadata() → only into the HTTP body."""
    exc = PatientAlreadyExistsError(
        patient_id="X",
        patient_name="Иванов Иван Иванович",
    )
    assert exc.metadata() == {
        "patient_id": "X",
        "patient_name": "Иванов Иван Иванович",
    }


def test_patient_already_exists_metadata_without_name():
    """Backward-compat: instantiation without patient_name omits the key."""
    exc = PatientAlreadyExistsError(patient_id="X")
    assert exc.metadata() == {"patient_id": "X"}


def test_entity_already_exists_default_code_and_empty_metadata():
    """Base class default: ENTITY_ALREADY_EXISTS + empty metadata()."""
    exc = EntityAlreadyExistsError("some text")
    assert exc.error_code == "ENTITY_ALREADY_EXISTS"
    assert exc.metadata() == {}
