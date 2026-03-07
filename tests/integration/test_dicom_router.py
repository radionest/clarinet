"""Integration tests for DICOM router against a live Orthanc PACS server.

These tests require a running Orthanc instance at PACS_HOST:PACS_PORT
with known test data pre-loaded. They are skipped automatically if the
server is unreachable.

Run:
    uv run pytest tests/integration/test_dicom_router.py -v
    uv run pytest -m dicom -v
"""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pydicom
import pytest
import pytest_asyncio
import requests
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from clarinet.api.app import app
from clarinet.api.dependencies import get_dicom_client, get_pacs_node
from clarinet.models.patient import Patient
from clarinet.models.study import Series, Study
from clarinet.services.dicom import DicomClient, DicomNode, SeriesQuery, StudyQuery
from clarinet.services.dicom.models import StudyResult
from clarinet.settings import settings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACS_HOST = "192.168.122.151"
PACS_PORT = 4242
PACS_AET = "ORTHANC"
PACS_REST_URL = "http://192.168.122.151:8042"
CALLING_AET = "CLARINET_TEST"

DICOM_BASE = "/api/dicom"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delete_study_from_pacs(study_uid: str) -> None:
    """Delete study from Orthanc PACS via REST API (best-effort cleanup)."""
    try:
        resp = requests.post(f"{PACS_REST_URL}/tools/lookup", data=study_uid, timeout=5)
        resp.raise_for_status()
        for item in resp.json():
            if item.get("Type") == "Study":
                requests.delete(f"{PACS_REST_URL}{item['Path']}", timeout=5)
    except requests.RequestException:
        pass  # Best-effort: don't fail tests if cleanup fails


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pacs_available() -> None:
    """Skip the entire session if the PACS server is unreachable."""
    try:
        resp = requests.get(f"{PACS_REST_URL}/system", timeout=2)
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
        pytest.skip("Orthanc PACS server is not reachable — skipping DICOM tests")


@pytest.fixture(scope="session")
def pacs_patient_id(pacs_available: None) -> str:
    """Fetch patient_id from the first SHIPILOV study on PACS."""
    client = DicomClient(calling_aet=CALLING_AET)
    node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)

    studies: list[StudyResult] = asyncio.run(
        client.find_studies(StudyQuery(patient_name="SHIPILOV*"), node)
    )
    assert studies, "No SHIPILOV studies found on test PACS"
    patient_id = studies[0].patient_id
    assert patient_id, "Study has no patient_id"
    return patient_id


@pytest.fixture(scope="session")
def pacs_study(pacs_available: None) -> StudyResult:
    """Fetch the first SHIPILOV study from PACS (for import tests)."""
    client = DicomClient(calling_aet=CALLING_AET)
    node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)

    studies: list[StudyResult] = asyncio.run(
        client.find_studies(StudyQuery(patient_name="SHIPILOV*"), node)
    )
    assert studies, "No SHIPILOV studies found on test PACS"
    return studies[0]


@pytest_asyncio.fixture(autouse=True)
async def override_dicom_deps() -> AsyncGenerator[None]:
    """Override DICOM DI dependencies to point at the test PACS."""
    app.dependency_overrides[get_dicom_client] = lambda: DicomClient(calling_aet=CALLING_AET)
    app.dependency_overrides[get_pacs_node] = lambda: DicomNode(
        aet=PACS_AET, host=PACS_HOST, port=PACS_PORT
    )
    yield
    app.dependency_overrides.pop(get_dicom_client, None)
    app.dependency_overrides.pop(get_pacs_node, None)


@pytest_asyncio.fixture
async def admin_logged_in(client: AsyncClient, admin_user: object) -> AsyncClient:
    """Log in as admin and return the client with cookies set."""
    response = await client.post(
        "/api/auth/login",
        data={"username": "admin@example.com", "password": "adminpassword"},
    )
    assert response.status_code in [200, 204]
    return client


@pytest_asyncio.fixture
async def db_patient(test_session: AsyncSession, pacs_patient_id: str) -> Patient:
    """Create a Patient record in the test DB matching the PACS patient_id."""
    patient = Patient(id=pacs_patient_id, name="SHIPILOV TEST")
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


# ===========================================================================
# A. GET /api/dicom/patient/{patient_id}/studies
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_search_unauthenticated(
    unauthenticated_client: AsyncClient, pacs_available: None, pacs_patient_id: str
) -> None:
    """Unauthenticated request to search endpoint returns 401."""
    response = await unauthenticated_client.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
    assert response.status_code == 401


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_search_non_admin(
    unauthenticated_client: AsyncClient,
    test_user: object,
    pacs_available: None,
    pacs_patient_id: str,
) -> None:
    """Non-superuser request to search endpoint returns 403."""
    await unauthenticated_client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpassword"},
    )
    response = await unauthenticated_client.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
    assert response.status_code == 403


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_search_nonexistent_patient(
    admin_logged_in: AsyncClient, pacs_available: None
) -> None:
    """Searching for a patient not in PACS returns empty list."""
    response = await admin_logged_in.get(f"{DICOM_BASE}/patient/DOESNOTEXIST_99999/studies")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_search_returns_studies_with_series(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    pacs_patient_id: str,
    db_patient: Patient,
) -> None:
    """Real PACS patient returns studies with series, study_instance_uid, and study_date."""
    response = await admin_logged_in.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    for item in data:
        study = item["study"]
        assert study["study_instance_uid"]
        assert "study_date" in study
        assert isinstance(item["series"], list)


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_search_already_exists_flag(
    admin_logged_in: AsyncClient,
    test_session: AsyncSession,
    pacs_available: None,
    pacs_patient_id: str,
    pacs_study: StudyResult,
    db_patient: Patient,
) -> None:
    """Study already in local DB is returned with already_exists=True."""
    from datetime import UTC, datetime

    # Create the study in the local DB so it shows as already existing
    study = Study(
        study_uid=pacs_study.study_instance_uid,
        date=datetime.now(tz=UTC).date(),
        patient_id=pacs_patient_id,
    )
    test_session.add(study)
    await test_session.commit()

    response = await admin_logged_in.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
    assert response.status_code == 200
    data = response.json()

    matching = [
        item
        for item in data
        if item["study"]["study_instance_uid"] == pacs_study.study_instance_uid
    ]
    assert matching, "Expected the known study in the results"
    assert matching[0]["already_exists"] is True


# ===========================================================================
# B. POST /api/dicom/import-study
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_unauthenticated(
    unauthenticated_client: AsyncClient, pacs_available: None, pacs_study: StudyResult
) -> None:
    """Unauthenticated import request returns 401."""
    response = await unauthenticated_client.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": pacs_study.patient_id,
        },
    )
    assert response.status_code == 401


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_success(
    admin_logged_in: AsyncClient,
    test_session: AsyncSession,
    pacs_available: None,
    pacs_study: StudyResult,
    db_patient: Patient,
) -> None:
    """Importing a valid study creates it in DB and returns StudyRead with series."""
    response = await admin_logged_in.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": db_patient.id,
        },
    )
    assert response.status_code == 200
    data = response.json()

    assert data["study_uid"] == pacs_study.study_instance_uid
    assert data["patient_id"] == db_patient.id
    assert "series" in data
    assert isinstance(data["series"], list)
    assert len(data["series"]) >= 1

    # Verify study exists in DB
    db_study = await test_session.get(Study, pacs_study.study_instance_uid)
    assert db_study is not None
    assert db_study.patient_id == db_patient.id


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_study_not_in_pacs(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    db_patient: Patient,
) -> None:
    """Importing a study UID that doesn't exist in PACS returns 404."""
    response = await admin_logged_in.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": "1.2.3.4.5.6.7.8.9.FAKE_NONEXISTENT",
            "patient_id": db_patient.id,
        },
    )
    assert response.status_code == 404


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_patient_not_in_db(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    pacs_study: StudyResult,
) -> None:
    """Importing a study for a patient_id not in DB returns 404."""
    response = await admin_logged_in.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": "NO_SUCH_PATIENT_IN_DB",
        },
    )
    assert response.status_code == 404


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_duplicate_study(
    admin_logged_in: AsyncClient,
    test_session: AsyncSession,
    pacs_available: None,
    pacs_study: StudyResult,
    db_patient: Patient,
) -> None:
    """Importing the same study twice returns 409 conflict."""
    from datetime import UTC, datetime

    # Pre-create the study so the second import triggers StudyAlreadyExistsError
    study = Study(
        study_uid=pacs_study.study_instance_uid,
        date=datetime.now(tz=UTC).date(),
        patient_id=db_patient.id,
    )
    test_session.add(study)
    await test_session.commit()

    response = await admin_logged_in.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": db_patient.id,
        },
    )
    assert response.status_code == 409


# ===========================================================================
# C. Additional Auth & Field Tests
# ===========================================================================


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_non_admin(
    unauthenticated_client: AsyncClient,
    test_user: object,
    pacs_available: None,
    pacs_study: StudyResult,
) -> None:
    """Non-superuser import request returns 403."""
    await unauthenticated_client.post(
        "/api/auth/login",
        data={"username": "test@example.com", "password": "testpassword"},
    )
    response = await unauthenticated_client.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": pacs_study.patient_id,
        },
    )
    assert response.status_code == 403


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_search_study_fields(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    pacs_patient_id: str,
    db_patient: Patient,
) -> None:
    """Search response includes modalities_in_study, study_description, patient_name."""
    response = await admin_logged_in.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
    assert response.status_code == 200
    data = response.json()
    assert data

    study = data[0]["study"]
    assert "modalities_in_study" in study
    assert "study_description" in study
    assert "patient_name" in study


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_search_series_fields(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    pacs_patient_id: str,
    db_patient: Patient,
) -> None:
    """Each series in search response has modality, series_instance_uid, instance count."""
    response = await admin_logged_in.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
    assert response.status_code == 200
    data = response.json()
    assert data

    series_list = data[0]["series"]
    assert series_list
    for s in series_list:
        assert s["modality"] is not None
        assert s["series_instance_uid"]
        assert "number_of_series_related_instances" in s


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_creates_correct_series_count(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    pacs_study: StudyResult,
    db_patient: Patient,
) -> None:
    """Imported study's series count matches PACS series count."""
    # Get expected series count from PACS directly
    client = DicomClient(calling_aet=CALLING_AET)
    node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)
    pacs_series = await client.find_series(
        SeriesQuery(study_instance_uid=pacs_study.study_instance_uid), node
    )

    response = await admin_logged_in.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": db_patient.id,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["series"]) == len(pacs_series)


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_import_series_descriptions_stored(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    pacs_study: StudyResult,
    db_patient: Patient,
) -> None:
    """Imported series preserve series_description from PACS."""
    # Get expected descriptions from PACS
    client = DicomClient(calling_aet=CALLING_AET)
    node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)
    pacs_series = await client.find_series(
        SeriesQuery(study_instance_uid=pacs_study.study_instance_uid), node
    )
    pacs_descriptions = {s.series_instance_uid: s.series_description for s in pacs_series}

    response = await admin_logged_in.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": db_patient.id,
        },
    )
    assert response.status_code == 200
    data = response.json()

    for series in data["series"]:
        pacs_desc = pacs_descriptions.get(series["series_uid"])
        assert series["series_description"] == pacs_desc


# ===========================================================================
# D. POST /api/dicom/studies/{study_uid}/anonymize — E2E Anonymization
# ===========================================================================


@pytest_asyncio.fixture
async def db_patient_with_anon_id(test_session: AsyncSession, pacs_patient_id: str) -> Patient:
    """Create a Patient record with auto_id set (so anon_id is available)."""
    patient = Patient(id=pacs_patient_id, name="SHIPILOV TEST", auto_id=42)
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def imported_study(
    admin_logged_in: AsyncClient,
    pacs_study: StudyResult,
    db_patient_with_anon_id: Patient,
) -> str:
    """Import a study via endpoint and return its study_uid."""
    response = await admin_logged_in.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study.study_instance_uid,
            "patient_id": db_patient_with_anon_id.id,
        },
    )
    assert response.status_code == 200
    return pacs_study.study_instance_uid


@pytest_asyncio.fixture
async def anon_output_dir(tmp_path: Path) -> AsyncGenerator[Path]:
    """Provide a temporary storage dir for anonymized DICOM files.

    Overrides storage_path so that dcm_anon files land under tmp_path.
    """
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    original = settings.storage_path
    settings.storage_path = str(storage_dir)
    yield storage_dir
    settings.storage_path = original


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_anonymize_via_endpoint_save_to_disk(
    admin_logged_in: AsyncClient,
    test_session: AsyncSession,
    pacs_available: None,
    imported_study: str,
    anon_output_dir: Path,
    db_patient_with_anon_id: Patient,
) -> None:
    """Anonymize study with save_to_disk=True produces valid DICOM files on disk."""
    response = await admin_logged_in.post(
        f"{DICOM_BASE}/studies/{imported_study}/anonymize",
        json={"save_to_disk": True, "send_to_pacs": False},
    )
    assert response.status_code == 200
    data = response.json()

    assert data["instances_anonymized"] > 0
    assert data["instances_failed"] == 0
    assert data["anon_study_uid"].startswith("2.25.")

    anon_study_uid = data["anon_study_uid"]
    anon_patient_id = db_patient_with_anon_id.anon_id
    assert anon_patient_id is not None

    # Verify files on disk under {storage}/patient/study/series/dcm_anon/
    patient_dir = anon_output_dir / anon_patient_id
    assert patient_dir.exists(), f"Expected patient dir at {patient_dir}"

    study_dir = patient_dir / anon_study_uid
    assert study_dir.exists(), f"Expected study dir at {study_dir}"

    # dcm_anon files are inside series/dcm_anon/ subdirectories
    dcm_files = list(study_dir.rglob("dcm_anon/*.dcm"))
    assert len(dcm_files) == data["instances_anonymized"]

    # Verify each file is valid DICOM with anonymized tags
    for dcm_file in dcm_files:
        ds = pydicom.dcmread(dcm_file)
        assert ds.PatientID == anon_patient_id
        assert ds.StudyInstanceUID != imported_study
        assert ds.StudyInstanceUID == anon_study_uid

    # Verify DB: study.anon_uid and series.anon_uid are set
    test_session.expire_all()
    db_study = await test_session.get(Study, imported_study)
    assert db_study is not None
    assert db_study.anon_uid == anon_study_uid

    result = await test_session.execute(select(Series).where(Series.study_uid == imported_study))
    series_list = result.scalars().all()
    for s in series_list:
        assert s.anon_uid is not None, f"Series {s.series_uid} missing anon_uid"


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_anonymize_via_endpoint_send_to_pacs(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    imported_study: str,
    db_patient_with_anon_id: Patient,
) -> None:
    """Anonymize study with send_to_pacs=True sends data to PACS."""
    response = await admin_logged_in.post(
        f"{DICOM_BASE}/studies/{imported_study}/anonymize",
        json={"save_to_disk": False, "send_to_pacs": True},
    )
    assert response.status_code == 200
    data = response.json()
    anon_study_uid = data["anon_study_uid"]

    try:
        assert data["sent_to_pacs"] is True
        assert data["instances_anonymized"] > 0

        anon_patient_id = db_patient_with_anon_id.anon_id

        # Verify anonymized study is findable on PACS via C-FIND
        client = DicomClient(calling_aet=CALLING_AET)
        node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)
        found = await client.find_studies(StudyQuery(study_instance_uid=anon_study_uid), node)
        assert found, f"Anonymized study {anon_study_uid} not found on PACS"
        assert found[0].patient_id == anon_patient_id
    finally:
        _delete_study_from_pacs(anon_study_uid)


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_anonymize_idempotent(
    admin_logged_in: AsyncClient,
    pacs_available: None,
    imported_study: str,
    anon_output_dir: Path,
    db_patient_with_anon_id: Patient,
) -> None:
    """Running anonymization twice on the same study produces identical results."""
    # First run
    resp1 = await admin_logged_in.post(
        f"{DICOM_BASE}/studies/{imported_study}/anonymize",
        json={"save_to_disk": True, "send_to_pacs": False},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()

    # Second run
    resp2 = await admin_logged_in.post(
        f"{DICOM_BASE}/studies/{imported_study}/anonymize",
        json={"save_to_disk": True, "send_to_pacs": False},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()

    assert data1["anon_study_uid"] == data2["anon_study_uid"]
    assert data1["instances_anonymized"] == data2["instances_anonymized"]
    assert data2["instances_failed"] == 0

    # Files on disk should still be valid
    anon_patient_id = db_patient_with_anon_id.anon_id
    assert anon_patient_id is not None
    dcm_files = list(
        (anon_output_dir / anon_patient_id / data2["anon_study_uid"]).rglob("dcm_anon/*.dcm")
    )
    assert len(dcm_files) == data2["instances_anonymized"]


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_anonymize_study_not_in_db(
    admin_logged_in: AsyncClient,
    pacs_available: None,
) -> None:
    """Anonymizing a non-existent study returns 404."""
    response = await admin_logged_in.post(
        f"{DICOM_BASE}/studies/1.2.3.FAKE/anonymize",
        json={"save_to_disk": True, "send_to_pacs": False},
    )
    assert response.status_code == 404
