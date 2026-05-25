"""Integration tests for DICOM PatientID validation.

Backend enforces a strict DICOM LO pattern (``[A-Za-z0-9._-^]{1,64}``)
on both Pydantic body DTOs and path parameters — no whitespace allowed,
no client-side trim. Frontends (Gleam ``patient_form``) trim before
submit; CLI/curl callers must strip themselves.
"""

import pytest

from clarinet.exceptions.domain import InvalidPatientIdentifierError
from clarinet.models.patient import validate_patient_id
from tests.utils.urls import PATIENTS_BASE, STUDIES_BASE

# --- Unit-level helper tests ---


@pytest.mark.parametrize(
    "raw",
    [
        "PAT001",
        "A.B_C-D^E",
        "a" * 64,
        "abc.123_xyz-ABC^001",
    ],
)
def test_validate_patient_id_accepts_valid(raw):
    assert validate_patient_id(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        " PAT001",  # leading space — backend no longer trims
        "PAT001 ",  # trailing space
        "PAT 001",  # inner whitespace
        "PAT@001",
        "PAT#001",
        "PAT/001",
        "PAT\\001",
        "ПАТ001",  # noqa: RUF001 — non-ASCII (Cyrillic) test fixture
        "a" * 65,  # too long
    ],
)
def test_validate_patient_id_rejects_invalid(raw):
    with pytest.raises(InvalidPatientIdentifierError):
        validate_patient_id(raw)


def test_validate_patient_id_preserves_raw_input_in_error():
    with pytest.raises(InvalidPatientIdentifierError) as excinfo:
        validate_patient_id("PAT@001")
    assert excinfo.value.patient_id == "PAT@001"
    assert "DICOM" in excinfo.value.reason


# --- POST /api/patients body validation ---


@pytest.mark.asyncio
async def test_create_patient_accepts_valid_id(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT001", "patient_name": "Normal"},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "PAT001"


@pytest.mark.asyncio
async def test_create_patient_rejects_whitespace_in_body(client):
    """No backend trim: a body with a stray space is rejected with structured 422."""
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": " PAT_LEAD", "patient_name": "Leading Space"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_PATIENT_IDENTIFIER"
    assert body["metadata"]["patient_id"] == " PAT_LEAD"


@pytest.mark.asyncio
async def test_create_patient_rejects_inner_whitespace(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT INNER", "patient_name": "Inner Space"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_PATIENT_IDENTIFIER"
    assert body["metadata"]["patient_id"] == "PAT INNER"
    assert "DICOM" in body["metadata"]["reason"]


@pytest.mark.asyncio
async def test_create_patient_rejects_invalid_chars(client):
    for bad in ("PAT@001", "PAT#001", "PAT/001"):
        response = await client.post(
            PATIENTS_BASE,
            json={"patient_id": bad, "patient_name": "Bad ID"},
        )
        assert response.status_code == 422, f"Expected 422 for {bad!r}"
        assert response.json()["code"] == "INVALID_PATIENT_IDENTIFIER"


@pytest.mark.asyncio
async def test_invalid_patient_id_response_excludes_pii_from_detail(client):
    """``detail`` carries only the reason; PII (raw id) lives in metadata."""
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT@LEAK", "patient_name": "PII Check"},
    )
    assert response.status_code == 422
    body = response.json()
    assert "PAT@LEAK" not in body["detail"]
    assert body["metadata"]["patient_id"] == "PAT@LEAK"


@pytest.mark.asyncio
async def test_create_patient_rejects_too_long(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "a" * 65, "patient_name": "Too Long"},
    )
    # Pydantic max_length=64 fires first → standard 422 body shape.
    assert response.status_code == 422


# --- Path-parameter validation (FastAPI ``Path(pattern=...)``) ---


@pytest.mark.asyncio
async def test_get_patient_rejects_invalid_path(client):
    """Strict ``Path`` pattern rejects malformed IDs at the FastAPI layer (422)."""
    response = await client.get(f"{PATIENTS_BASE}/PAT@INVALID")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_patient_rejects_whitespace_in_path(client):
    """Strict path pattern — trailing space in URL no longer accepted."""
    response = await client.get(f"{PATIENTS_BASE}/PAT001%20")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_patient_rejects_invalid_path(client):
    response = await client.delete(f"{PATIENTS_BASE}/PAT@DEL")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_anonymize_patient_rejects_invalid_path(client):
    response = await client.post(f"{PATIENTS_BASE}/PAT@ANON/anonymize")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_file_events_rejects_invalid_patient_id_path(client):
    response = await client.post(
        f"{PATIENTS_BASE}/PAT@FILE/file-events",
        json=["some_file.nii.gz"],
    )
    assert response.status_code == 422


# --- POST /api/studies body validation (patient_id in payload) ---


@pytest.mark.asyncio
async def test_create_study_rejects_invalid_patient_id(client):
    response = await client.post(
        STUDIES_BASE,
        json={
            "study_uid": "1.2.3.4.5.6.7.8.10",
            "patient_id": "PAT@STUDY",
            "date": "2026-01-01",
        },
    )
    assert response.status_code == 422


# --- Pydantic-level: PatientSave DTO direct construction ---


def test_patient_save_dto_rejects_whitespace():
    """Direct PatientSave(...) — validator surfaces the domain exception."""
    from clarinet.models.patient import PatientSave

    with pytest.raises(InvalidPatientIdentifierError):
        PatientSave(patient_id=" PAT_DTO ", patient_name="DTO Direct")
