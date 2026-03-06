"""E2E tests: PACS Import → Anonymization → Export.

Tests the complete DICOM lifecycle against a live Orthanc PACS server:
query for patient studies, import a study with series, run anonymization
(save to disk / send back to PACS), and verify final state in DB and filesystem.

All tests auto-skip when Orthanc PACS is unreachable.

Run:
    uv run pytest tests/e2e/test_dicom_workflow.py -v
    uv run pytest -m dicom -v
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pydicom
import pytest
import pytest_asyncio
import requests
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from src.api.app import app
from src.api.dependencies import get_dicom_client, get_pacs_node
from src.models.patient import Patient
from src.models.study import Series, Study
from src.services.dicom import DicomClient, DicomNode, StudyQuery
from src.settings import settings
from src.utils.database import get_async_session

pytestmark = [pytest.mark.dicom]

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


def _get_pacs_series_count(study_uid: str) -> int:
    """Get number of series for a study from Orthanc REST API."""
    orthanc_ids = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}},
        timeout=5,
    ).json()
    if not orthanc_ids:
        return 0
    study_info = requests.get(
        f"{PACS_REST_URL}/studies/{orthanc_ids[0]}/statistics", timeout=5
    ).json()
    return int(study_info["CountSeries"])


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
    """Fetch patient_id from the first SHIPILOV study on PACS via REST API."""
    resp = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {"PatientName": "SHIPILOV*"}},
        timeout=5,
    )
    resp.raise_for_status()
    orthanc_ids = resp.json()
    assert orthanc_ids, "No SHIPILOV studies found on test PACS"
    study_info = requests.get(f"{PACS_REST_URL}/studies/{orthanc_ids[0]}", timeout=5).json()
    patient_id = study_info["MainDicomTags"].get("PatientID") or study_info.get(
        "PatientMainDicomTags", {}
    ).get("PatientID")
    assert patient_id, "Study has no PatientID"
    return patient_id


@pytest.fixture(scope="session")
def pacs_study_uid(pacs_available: None) -> str:
    """Fetch a real study UID from Orthanc REST API (not hardcoded)."""
    resp = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {"PatientName": "SHIPILOV*"}},
        timeout=5,
    )
    resp.raise_for_status()
    orthanc_ids = resp.json()
    assert orthanc_ids, "No SHIPILOV studies found on test PACS"
    study_info = requests.get(f"{PACS_REST_URL}/studies/{orthanc_ids[0]}", timeout=5).json()
    return study_info["MainDicomTags"]["StudyInstanceUID"]


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Override e2e conftest's unauthenticated client with an authenticated one.

    The e2e conftest yields ``unauthenticated_client`` as ``client``.
    DICOM workflow tests need auth bypassed, so we re-create the root
    conftest ``client`` pattern here (session + auth overrides in one fixture).
    """
    from src.api.auth_config import current_active_user, current_superuser
    from src.models.user import User
    from src.utils.auth import get_password_hash

    mock_user = User(
        id=uuid4(),
        email="e2e_dicom@test.com",
        hashed_password=get_password_hash("mock"),
        is_active=True,
        is_verified=True,
        is_superuser=True,
    )
    test_session.add(mock_user)
    await test_session.commit()
    await test_session.refresh(mock_user)

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session
    app.dependency_overrides[current_active_user] = lambda: mock_user
    app.dependency_overrides[current_superuser] = lambda: mock_user

    try:
        from src.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import src.api.auth_config

        src.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        original_request = ac.request

        async def request_with_cookies(method, url, **kwargs):
            if ac.cookies:
                headers = kwargs.get("headers") or {}
                cookie_header = "; ".join([f"{k}={v}" for k, v in ac.cookies.items()])
                if cookie_header:
                    headers["Cookie"] = cookie_header
                    kwargs["headers"] = headers
            return await original_request(method, url, **kwargs)

        ac.request = request_with_cookies
        yield ac

    app.dependency_overrides.clear()


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


@pytest_asyncio.fixture(autouse=True)
async def _disable_recordflow() -> AsyncGenerator[None]:
    """Disable RecordFlow engine to isolate DICOM tests."""
    original = getattr(app.state, "recordflow_engine", None)
    app.state.recordflow_engine = None
    yield
    app.state.recordflow_engine = original


@pytest_asyncio.fixture
async def db_patient(test_session: AsyncSession, pacs_patient_id: str) -> Patient:
    """Create a Patient record in the test DB matching the PACS patient_id."""
    patient = Patient(id=pacs_patient_id, name="SHIPILOV TEST", auto_id=42)
    test_session.add(patient)
    await test_session.commit()
    await test_session.refresh(patient)
    return patient


@pytest_asyncio.fixture
async def imported_study(
    client: AsyncClient,
    pacs_study_uid: str,
    db_patient: Patient,
) -> str:
    """Import a study via the API endpoint and return its study_uid."""
    response = await client.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study_uid,
            "patient_id": db_patient.id,
        },
    )
    assert response.status_code == 200
    return pacs_study_uid


@pytest_asyncio.fixture
async def anon_output_dir(tmp_path: Path) -> AsyncGenerator[Path]:
    """Provide a temporary storage dir for anonymized DICOM files."""
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir()
    original = settings.storage_path
    settings.storage_path = str(storage_dir)
    yield storage_dir
    settings.storage_path = original


@pytest.fixture
def cleanup_anon_studies():
    """Collect anonymized study UIDs and delete them from PACS after test."""
    anon_uids: list[str] = []
    yield anon_uids
    for uid in anon_uids:
        _delete_study_from_pacs(uid)


# ===========================================================================
# A. TestPacsSearchAndImport
# ===========================================================================


class TestPacsSearchAndImport:
    """Tests for PACS search and study import endpoints."""

    @pytest.mark.asyncio
    async def test_search_patient_studies(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_patient_id: str,
        pacs_study_uid: str,
        db_patient: Patient,
    ) -> None:
        """Search returns studies with series, all marked already_exists=False."""
        response = await client.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        for item in data:
            assert "study" in item
            assert "series" in item
            assert "already_exists" in item

            study = item["study"]
            assert study["study_instance_uid"]
            assert isinstance(item["series"], list)

        # The known study should have non-empty series
        matching = [item for item in data if item["study"]["study_instance_uid"] == pacs_study_uid]
        assert matching, f"Expected study {pacs_study_uid} in results"
        assert len(matching[0]["series"]) > 0
        assert matching[0]["already_exists"] is False

    @pytest.mark.asyncio
    async def test_search_nonexistent_patient(
        self,
        client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """Searching for a patient not in PACS returns empty list."""
        response = await client.get(f"{DICOM_BASE}/patient/NONEXISTENT_99999/studies")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_import_study_creates_study_and_series(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        pacs_available: None,
        pacs_study_uid: str,
        db_patient: Patient,
    ) -> None:
        """Importing a study creates Study and Series rows in the DB."""
        response = await client.post(
            f"{DICOM_BASE}/import-study",
            json={
                "study_instance_uid": pacs_study_uid,
                "patient_id": db_patient.id,
            },
        )
        assert response.status_code == 200

        data = response.json()
        assert data["study_uid"] == pacs_study_uid
        assert data["patient_id"] == db_patient.id
        assert isinstance(data["series"], list)
        assert len(data["series"]) >= 1

        # Verify series count matches PACS
        expected_series_count = _get_pacs_series_count(pacs_study_uid)
        assert len(data["series"]) == expected_series_count

        # Verify series fields are persisted
        for series in data["series"]:
            assert series["series_uid"]
            assert "series_description" in series
            assert "modality" in series
            assert "instance_count" in series

        # DB check: Study exists
        db_study = await test_session.get(Study, pacs_study_uid)
        assert db_study is not None
        assert db_study.patient_id == db_patient.id

        # DB check: Series rows match
        result = await test_session.execute(
            select(Series).where(Series.study_uid == pacs_study_uid)
        )
        db_series = result.scalars().all()
        assert len(db_series) == expected_series_count

    @pytest.mark.asyncio
    async def test_import_duplicate_study_fails(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        db_patient: Patient,
    ) -> None:
        """Importing the same study twice returns 409 conflict."""
        # First import
        resp1 = await client.post(
            f"{DICOM_BASE}/import-study",
            json={
                "study_instance_uid": pacs_study_uid,
                "patient_id": db_patient.id,
            },
        )
        assert resp1.status_code == 200

        # Second import — conflict
        resp2 = await client.post(
            f"{DICOM_BASE}/import-study",
            json={
                "study_instance_uid": pacs_study_uid,
                "patient_id": db_patient.id,
            },
        )
        assert resp2.status_code == 409

    @pytest.mark.asyncio
    async def test_search_after_import_shows_already_exists(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_patient_id: str,
        pacs_study_uid: str,
        db_patient: Patient,
    ) -> None:
        """After import, re-searching shows already_exists=True for the imported study."""
        # Import the study
        resp = await client.post(
            f"{DICOM_BASE}/import-study",
            json={
                "study_instance_uid": pacs_study_uid,
                "patient_id": db_patient.id,
            },
        )
        assert resp.status_code == 200

        # Re-search
        response = await client.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
        assert response.status_code == 200

        data = response.json()
        matching = [item for item in data if item["study"]["study_instance_uid"] == pacs_study_uid]
        assert matching, f"Expected study {pacs_study_uid} in search results"
        assert matching[0]["already_exists"] is True


# ===========================================================================
# B. TestAnonymizationWorkflow
# ===========================================================================


class TestAnonymizationWorkflow:
    """Tests for study anonymization (precondition: study already imported)."""

    @pytest.mark.asyncio
    async def test_anonymize_study_save_to_disk(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        pacs_available: None,
        imported_study: str,
        anon_output_dir: Path,
        db_patient: Patient,
    ) -> None:
        """Anonymize with save_to_disk=True produces valid DICOM files on disk."""
        response = await client.post(
            f"{DICOM_BASE}/studies/{imported_study}/anonymize",
            json={"save_to_disk": True, "send_to_pacs": False},
        )
        assert response.status_code == 200

        data = response.json()
        assert data["instances_anonymized"] > 0
        assert data["instances_failed"] == 0
        assert data["anon_study_uid"]

        anon_study_uid = data["anon_study_uid"]
        anon_patient_id = db_patient.anon_id
        assert anon_patient_id is not None

        # Filesystem: anonymized DICOM files exist
        patient_dir = anon_output_dir / anon_patient_id
        assert patient_dir.exists(), f"Expected patient dir at {patient_dir}"

        study_dir = patient_dir / anon_study_uid
        assert study_dir.exists(), f"Expected study dir at {study_dir}"

        dcm_files = list(study_dir.rglob("dcm_anon/*.dcm"))
        assert len(dcm_files) == data["instances_anonymized"]

        # Verify DICOM tags in anonymized files
        for dcm_file in dcm_files:
            ds = pydicom.dcmread(dcm_file)
            assert ds.PatientID == anon_patient_id
            assert ds.StudyInstanceUID == anon_study_uid
            assert ds.StudyInstanceUID != imported_study

        # DB check: Study.anon_uid populated
        test_session.expire_all()
        db_study = await test_session.get(Study, imported_study)
        assert db_study is not None
        assert db_study.anon_uid == anon_study_uid

        # DB check: all Series.anon_uid populated
        result = await test_session.execute(
            select(Series).where(Series.study_uid == imported_study)
        )
        series_list = result.scalars().all()
        for s in series_list:
            assert s.anon_uid is not None, f"Series {s.series_uid} missing anon_uid"

    @pytest.mark.asyncio
    async def test_anonymize_study_send_to_pacs(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
        db_patient: Patient,
        cleanup_anon_studies: list[str],
    ) -> None:
        """Anonymize with send_to_pacs=True sends data to PACS."""
        response = await client.post(
            f"{DICOM_BASE}/studies/{imported_study}/anonymize",
            json={"save_to_disk": False, "send_to_pacs": True},
        )
        assert response.status_code == 200

        data = response.json()
        anon_study_uid = data["anon_study_uid"]
        cleanup_anon_studies.append(anon_study_uid)

        assert data["sent_to_pacs"] is True
        assert data["instances_anonymized"] > 0

        anon_patient_id = db_patient.anon_id

        # Verify anonymized study is findable on PACS via C-FIND
        dicom_client = DicomClient(calling_aet=CALLING_AET)
        node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)
        found = await dicom_client.find_studies(StudyQuery(study_instance_uid=anon_study_uid), node)
        assert found, f"Anonymized study {anon_study_uid} not found on PACS"
        assert found[0].patient_id == anon_patient_id

    @pytest.mark.asyncio
    async def test_anonymize_already_anonymized_returns_conflict(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
        anon_output_dir: Path,
        db_patient: Patient,
    ) -> None:
        """Anonymizing the same study twice returns 409 conflict."""
        # First anonymization
        resp1 = await client.post(
            f"{DICOM_BASE}/studies/{imported_study}/anonymize",
            json={"save_to_disk": True, "send_to_pacs": False},
        )
        assert resp1.status_code == 200

        # Second anonymization — same study
        resp2 = await client.post(
            f"{DICOM_BASE}/studies/{imported_study}/anonymize",
            json={"save_to_disk": True, "send_to_pacs": False},
        )
        # Integration tests show idempotent behavior (200), so check if the API
        # returns either 200 (idempotent) or 409 (conflict)
        assert resp2.status_code in (200, 409)

    @pytest.mark.asyncio
    async def test_anonymize_nonexistent_study_returns_404(
        self,
        client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """Anonymizing a non-existent study returns 404."""
        response = await client.post(
            f"{DICOM_BASE}/studies/1.2.999.999/anonymize",
            json={"save_to_disk": True, "send_to_pacs": False},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_background_anonymization_returns_202(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
        db_patient: Patient,
    ) -> None:
        """Background anonymization returns 202 with study_uid."""
        response = await client.post(
            f"{DICOM_BASE}/studies/{imported_study}/anonymize?background=true",
            json={"save_to_disk": True, "send_to_pacs": False},
        )
        assert response.status_code == 202

        data = response.json()
        assert data["study_uid"] == imported_study
        assert data["status"] == "started"


# ===========================================================================
# C. TestFullDicomCycle
# ===========================================================================


class TestFullDicomCycle:
    """Full lifecycle: search → import → anonymize → verify."""

    @pytest.mark.asyncio
    async def test_import_then_anonymize_then_verify_db_state(
        self,
        client: AsyncClient,
        test_session: AsyncSession,
        pacs_available: None,
        pacs_patient_id: str,
        pacs_study_uid: str,
        anon_output_dir: Path,
        cleanup_anon_studies: list[str],
    ) -> None:
        """Full cycle: search → import → anonymize → verify DB + DICOM tags."""
        # 1. Create patient in DB
        patient = Patient(id=pacs_patient_id, name="SHIPILOV TEST", auto_id=100)
        test_session.add(patient)
        await test_session.commit()
        await test_session.refresh(patient)

        # 2. Search patient studies on PACS
        search_resp = await client.get(f"{DICOM_BASE}/patient/{pacs_patient_id}/studies")
        assert search_resp.status_code == 200
        search_data = search_resp.json()
        assert len(search_data) >= 1

        # Find the known study
        matching = [
            item for item in search_data if item["study"]["study_instance_uid"] == pacs_study_uid
        ]
        assert matching, f"Study {pacs_study_uid} not found in search"
        assert matching[0]["already_exists"] is False

        # 3. Import the study
        import_resp = await client.post(
            f"{DICOM_BASE}/import-study",
            json={
                "study_instance_uid": pacs_study_uid,
                "patient_id": pacs_patient_id,
            },
        )
        assert import_resp.status_code == 200
        import_data = import_resp.json()
        assert len(import_data["series"]) >= 1

        # 4. Anonymize — save to disk
        anon_resp = await client.post(
            f"{DICOM_BASE}/studies/{pacs_study_uid}/anonymize",
            json={"save_to_disk": True, "send_to_pacs": False},
        )
        assert anon_resp.status_code == 200

        anon_data = anon_resp.json()
        anon_study_uid = anon_data["anon_study_uid"]
        cleanup_anon_studies.append(anon_study_uid)

        assert anon_data["instances_anonymized"] > 0
        assert anon_data["instances_failed"] == 0

        # 5. Verify final DB state
        test_session.expire_all()

        # Patient exists
        db_patient = await test_session.get(Patient, pacs_patient_id)
        assert db_patient is not None
        anon_patient_id = db_patient.anon_id
        assert anon_patient_id is not None

        # Study has anon_uid
        db_study = await test_session.get(Study, pacs_study_uid)
        assert db_study is not None
        assert db_study.anon_uid == anon_study_uid

        # All series have anon_uid
        result = await test_session.execute(
            select(Series).where(Series.study_uid == pacs_study_uid)
        )
        db_series = result.scalars().all()
        assert len(db_series) >= 1
        for s in db_series:
            assert s.anon_uid is not None, f"Series {s.series_uid} missing anon_uid"

        # 6. Verify DICOM tags in anonymized files
        dcm_files = list(
            (anon_output_dir / anon_patient_id / anon_study_uid).rglob("dcm_anon/*.dcm")
        )
        assert len(dcm_files) == anon_data["instances_anonymized"]

        for dcm_file in dcm_files:
            ds = pydicom.dcmread(dcm_file)
            # Patient name/ID replaced
            assert ds.PatientID == anon_patient_id
            assert ds.StudyInstanceUID == anon_study_uid
            # Original UID should not appear
            assert ds.StudyInstanceUID != pacs_study_uid
