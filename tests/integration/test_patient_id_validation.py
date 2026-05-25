"""Integration tests for DICOM PatientID normalization and validation.

Covers the trim-on-write contract and the regex check enforced in
``clarinet.services.study_service.StudyService.create_patient`` /
``get_patient`` via ``clarinet.models.patient.normalize_patient_id``.
"""

import pytest

from clarinet.exceptions.domain import InvalidPatientIdentifierError
from clarinet.models.patient import normalize_patient_id
from tests.utils.urls import PATIENTS_BASE

# --- Unit-level helper tests ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("PAT001", "PAT001"),
        (" PAT001", "PAT001"),
        ("PAT001 ", "PAT001"),
        ("\tPAT001\n", "PAT001"),
        ("A.B_C-D^E", "A.B_C-D^E"),
        ("a" * 64, "a" * 64),
    ],
)
def test_normalize_patient_id_accepts_and_trims(raw, expected):
    assert normalize_patient_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "PAT 001",  # inner whitespace
        "PAT@001",
        "PAT#001",
        "PAT/001",
        "PAT\\001",
        "ПАТ001",  # noqa: RUF001 — non-ASCII (Cyrillic) test fixture
        "a" * 65,  # too long
    ],
)
def test_normalize_patient_id_rejects_invalid(raw):
    with pytest.raises(InvalidPatientIdentifierError):
        normalize_patient_id(raw)


def test_normalize_patient_id_preserves_raw_input_in_error():
    with pytest.raises(InvalidPatientIdentifierError) as excinfo:
        normalize_patient_id("PAT@001")
    assert excinfo.value.patient_id == "PAT@001"
    assert "DICOM" in excinfo.value.reason


# --- Integration tests: POST /api/patients (create) ---


@pytest.mark.asyncio
async def test_create_patient_trims_leading_whitespace(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": " PAT_LEAD", "patient_name": "Leading Space"},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "PAT_LEAD"


@pytest.mark.asyncio
async def test_create_patient_trims_trailing_whitespace(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT_TAIL ", "patient_name": "Trailing Space"},
    )
    assert response.status_code == 201
    assert response.json()["id"] == "PAT_TAIL"


@pytest.mark.asyncio
async def test_create_patient_rejects_inner_whitespace(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT INNER", "patient_name": "Inner Space"},
    )
    assert response.status_code == 422
    assert "DICOM" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_patient_rejects_empty_after_trim(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "   ", "patient_name": "Empty After Trim"},
    )
    # PatientSave has min_length=1 → Pydantic 422 first when literally empty
    # after whitespace stripping isn't applied. But our payload " " has length
    # 3, so it passes Pydantic and hits normalize_patient_id, which raises
    # InvalidPatientIdentifierError → 422.
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_patient_rejects_invalid_chars(client):
    for bad in ("PAT@001", "PAT#001", "PAT/001"):
        response = await client.post(
            PATIENTS_BASE,
            json={"patient_id": bad, "patient_name": "Bad ID"},
        )
        assert response.status_code == 422, f"Expected 422 for {bad!r}"


@pytest.mark.asyncio
async def test_create_patient_rejects_too_long(client):
    too_long = "a" * 65
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": too_long, "patient_name": "Too Long"},
    )
    # Pydantic max_length=64 catches this before normalize_patient_id runs.
    assert response.status_code == 422


# --- Integration tests: GET /api/patients/{id} (read symmetry) ---


@pytest.mark.asyncio
async def test_get_patient_trims_path_whitespace(client):
    create = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT_SYMM", "patient_name": "Symmetry"},
    )
    assert create.status_code == 201

    # Path with trailing space (URL-encoded as %20) — read-side normalization
    # tolerates it and finds the clean stored ID.
    response = await client.get(f"{PATIENTS_BASE}/PAT_SYMM%20")
    assert response.status_code == 200
    assert response.json()["id"] == "PAT_SYMM"


@pytest.mark.asyncio
async def test_get_patient_rejects_invalid_path(client):
    response = await client.get(f"{PATIENTS_BASE}/PAT@INVALID")
    assert response.status_code == 422
