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
    body = response.json()
    assert body["code"] == "INVALID_PATIENT_IDENTIFIER"
    assert body["metadata"]["patient_id"] == "PAT INNER"
    assert "DICOM" in body["metadata"]["reason"]


@pytest.mark.asyncio
async def test_create_patient_rejects_empty_after_trim(client):
    response = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "   ", "patient_name": "Empty After Trim"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_PATIENT_IDENTIFIER"
    assert body["metadata"]["reason"] == "empty after trimming whitespace"


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


# --- Sibling endpoints share the same normalization ---


@pytest.mark.asyncio
async def test_delete_patient_trims_path_whitespace(client):
    create = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT_DEL", "patient_name": "To Delete"},
    )
    assert create.status_code == 201

    response = await client.delete(f"{PATIENTS_BASE}/PAT_DEL%20")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_anonymize_patient_trims_path_whitespace(client):
    create = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT_ANON", "patient_name": "To Anonymize"},
    )
    assert create.status_code == 201

    response = await client.post(f"{PATIENTS_BASE}/PAT_ANON%20/anonymize")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_study_trims_patient_id(client):
    create = await client.post(
        PATIENTS_BASE,
        json={"patient_id": "PAT_STUDY", "patient_name": "Study Owner"},
    )
    assert create.status_code == 201

    from tests.utils.urls import STUDIES_BASE

    response = await client.post(
        STUDIES_BASE,
        json={
            "study_uid": "1.2.3.4.5.6.7.8.9",
            "patient_id": " PAT_STUDY ",
            "date": "2026-01-01",
        },
    )
    assert response.status_code == 201
    assert response.json()["patient_id"] == "PAT_STUDY"


@pytest.mark.asyncio
async def test_create_study_rejects_invalid_patient_id(client):
    from tests.utils.urls import STUDIES_BASE

    response = await client.post(
        STUDIES_BASE,
        json={
            "study_uid": "1.2.3.4.5.6.7.8.10",
            "patient_id": "PAT@STUDY",
            "date": "2026-01-01",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_file_events_rejects_invalid_patient_id(client):
    """RecordService.notify_file_updates shares the same trim+regex contract."""
    response = await client.post(
        f"{PATIENTS_BASE}/PAT@FILE/file-events",
        json=["some_file.nii.gz"],
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PATIENT_IDENTIFIER"


def test_patient_save_pydantic_trims_id():
    """PatientSave (API entry DTO) trims via the inherited field_validator.

    Direct ``Patient(table=True)(id=...)`` construction is NOT covered —
    SQLModel bypasses Pydantic ``__init__`` validators for table models.
    All public paths reach this via the service layer instead.
    """
    from clarinet.models.patient import PatientSave

    payload = PatientSave(patient_id=" PAT_DTO ", patient_name="DTO Direct")
    assert payload.id == "PAT_DTO"
