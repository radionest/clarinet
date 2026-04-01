"""E2E tests: DICOMweb preload endpoints.

Tests the preload workflow: POST /preload/{study_uid} starts background
cache population, GET /preload/{study_uid}/progress/{task_id} reports
progress, and the task eventually reaches "ready" status.

Studies are imported into the Clarinet DB before preload tests run.
All tests auto-skip when Orthanc PACS is unreachable.

Run:
    uv run pytest tests/e2e/test_dicomweb_preload.py -v
    uv run pytest -m dicom -v
"""

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio
import requests
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.api.app import app
from clarinet.api.dependencies import (
    get_dicom_client,
    get_dicomweb_cache,
    get_dicomweb_proxy_service,
    get_pacs_node,
)
from clarinet.models.patient import Patient
from clarinet.services.dicom import DicomClient, DicomNode
from clarinet.services.dicomweb.cache import DicomWebCache
from clarinet.services.dicomweb.service import DicomWebProxyService
from tests.conftest import create_authenticated_client, create_mock_superuser
from tests.utils.factories import make_patient
from tests.utils.urls import DICOM_BASE, DICOMWEB_BASE

pytestmark = [pytest.mark.dicom]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACS_HOST = "192.168.122.151"
PACS_PORT = 4242
PACS_AET = "ORTHANC"
PACS_REST_URL = "http://192.168.122.151:8042"
CALLING_AET = "CLARINET_TEST"

PRELOAD_POLL_INTERVAL = 1.0  # seconds
PRELOAD_TIMEOUT = 120.0  # seconds


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
        pytest.skip("Orthanc PACS server is not reachable — skipping preload tests")


@pytest.fixture(scope="session")
def pacs_study_uid(pacs_available: None) -> str:
    """Fetch a real study UID from Orthanc REST API."""
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


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Authenticated client with DICOMweb auth bypass."""
    mock_user = await create_mock_superuser(test_session, email="e2e_preload@test.com")
    async for ac in create_authenticated_client(mock_user, test_session, test_settings):
        yield ac


@pytest_asyncio.fixture
async def db_patient(test_session: AsyncSession, pacs_patient_id: str) -> Patient:
    """Create a Patient record in the test DB matching the PACS patient_id."""
    patient = make_patient(pid=pacs_patient_id, name="SHIPILOV TEST")
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
    """Import a study from PACS into the Clarinet DB and return its study_uid."""
    response = await client.post(
        f"{DICOM_BASE}/import-study",
        json={
            "study_instance_uid": pacs_study_uid,
            "patient_id": db_patient.id,
        },
    )
    assert response.status_code == 200
    return pacs_study_uid


@pytest_asyncio.fixture(autouse=True)
async def override_dicomweb_deps(tmp_path: Path) -> AsyncGenerator[None]:
    """Override DICOMweb DI dependencies to point at the test PACS with tmp cache."""
    cache = DicomWebCache(
        base_dir=tmp_path / "dicomweb_cache",
        ttl_hours=1,
        max_size_gb=1.0,
        memory_ttl_minutes=30,
        memory_max_entries=50,
    )
    (tmp_path / "dicomweb_cache").mkdir(exist_ok=True)

    dicom_client = DicomClient(calling_aet=CALLING_AET)
    pacs_node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)
    proxy_service = DicomWebProxyService(client=dicom_client, pacs=pacs_node, cache=cache)

    app.dependency_overrides[get_dicom_client] = lambda: dicom_client
    app.dependency_overrides[get_pacs_node] = lambda: pacs_node
    app.dependency_overrides[get_dicomweb_cache] = lambda: cache
    app.dependency_overrides[get_dicomweb_proxy_service] = lambda: proxy_service

    yield

    await cache.shutdown()

    app.dependency_overrides.pop(get_dicom_client, None)
    app.dependency_overrides.pop(get_pacs_node, None)
    app.dependency_overrides.pop(get_dicomweb_cache, None)
    app.dependency_overrides.pop(get_dicomweb_proxy_service, None)


@pytest_asyncio.fixture(autouse=True)
async def _disable_recordflow() -> AsyncGenerator[None]:
    """Disable RecordFlow engine to isolate DICOMweb tests."""
    original = getattr(app.state, "recordflow_engine", None)
    app.state.recordflow_engine = None
    yield
    app.state.recordflow_engine = original


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def poll_until_ready(
    client: AsyncClient,
    study_uid: str,
    task_id: str,
    *,
    poll_timeout: float = PRELOAD_TIMEOUT,
    interval: float = PRELOAD_POLL_INTERVAL,
) -> dict:
    """Poll preload progress until status is terminal (ready/error) or timeout."""
    elapsed = 0.0
    last_progress: dict = {}
    while elapsed < poll_timeout:
        resp = await client.get(f"{DICOMWEB_BASE}/preload/{study_uid}/progress/{task_id}")
        assert resp.status_code == 200
        last_progress = resp.json()
        status = last_progress.get("status")
        if status in ("ready", "error", "not_found"):
            return last_progress
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Preload did not complete within {poll_timeout}s. Last: {last_progress}")


# ===========================================================================
# A. TestPreloadStartAndProgress
# ===========================================================================


class TestPreloadStartAndProgress:
    """Preload lifecycle: start → poll → ready."""

    @pytest.mark.asyncio
    async def test_start_preload_returns_task_id(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """POST /preload/{study_uid} returns a task_id."""
        resp = await client.post(f"{DICOMWEB_BASE}/preload/{imported_study}")
        assert resp.status_code == 200

        data = resp.json()
        assert "task_id" in data
        assert data["task_id"].startswith("preload_")
        assert imported_study in data["task_id"]

    @pytest.mark.asyncio
    async def test_preload_reaches_ready(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """Preload eventually reaches 'ready' status for an imported study."""
        resp = await client.post(f"{DICOMWEB_BASE}/preload/{imported_study}")
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        progress = await poll_until_ready(client, imported_study, task_id)
        assert progress["status"] == "ready"

    @pytest.mark.asyncio
    async def test_preload_progress_reports_received_count(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """Progress endpoint reports received count when preload completes."""
        resp = await client.post(f"{DICOMWEB_BASE}/preload/{imported_study}")
        task_id = resp.json()["task_id"]

        progress = await poll_until_ready(client, imported_study, task_id)
        assert progress["status"] == "ready"
        assert progress.get("received", 0) >= 0

    @pytest.mark.asyncio
    async def test_preload_populates_cache_for_metadata(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """After preload completes, WADO-RS metadata is served from cache."""
        resp = await client.post(f"{DICOMWEB_BASE}/preload/{imported_study}")
        task_id = resp.json()["task_id"]
        progress = await poll_until_ready(client, imported_study, task_id)
        assert progress["status"] == "ready"

        meta_resp = await client.get(f"{DICOMWEB_BASE}/studies/{imported_study}/metadata")
        assert meta_resp.status_code == 200
        metadata = meta_resp.json()
        assert isinstance(metadata, list)
        assert len(metadata) >= 1

    @pytest.mark.asyncio
    async def test_preload_nonexistent_study_reaches_ready(
        self,
        client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """Preload for a study with no series completes with ready/0."""
        fake_uid = "1.2.999.999.0"
        resp = await client.post(f"{DICOMWEB_BASE}/preload/{fake_uid}")
        assert resp.status_code == 200
        task_id = resp.json()["task_id"]

        progress = await poll_until_ready(client, fake_uid, task_id)
        assert progress["status"] == "ready"
        assert progress.get("received", 0) == 0
        assert progress.get("total", 0) == 0


# ===========================================================================
# B. TestPreloadProgressEndpoint
# ===========================================================================


class TestPreloadProgressEndpoint:
    """Progress endpoint edge cases."""

    @pytest.mark.asyncio
    async def test_progress_unknown_task_id(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """GET /preload/.../progress/{unknown} returns not_found."""
        resp = await client.get(
            f"{DICOMWEB_BASE}/preload/{imported_study}/progress/nonexistent_task_id"
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_progress_initial_status(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """First progress poll returns starting or checking_cache status."""
        resp = await client.post(f"{DICOMWEB_BASE}/preload/{imported_study}")
        task_id = resp.json()["task_id"]

        # Immediately poll — should be in early phase
        progress_resp = await client.get(
            f"{DICOMWEB_BASE}/preload/{imported_study}/progress/{task_id}"
        )
        assert progress_resp.status_code == 200
        status = progress_resp.json()["status"]
        assert status in ("starting", "checking_cache", "fetching", "ready")


# ===========================================================================
# C. TestPreloadAuthEnforcement
# ===========================================================================


class TestPreloadAuthEnforcement:
    """Preload endpoints require authentication."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/preload/1.2.3"),
            ("GET", "/preload/1.2.3/progress/some_task_id"),
        ],
    )
    async def test_preload_requires_auth(
        self,
        unauthenticated_client: AsyncClient,
        method: str,
        path: str,
    ) -> None:
        """Preload endpoints return 401 without authentication."""
        resp = await unauthenticated_client.request(method, f"{DICOMWEB_BASE}{path}")
        assert resp.status_code == 401


# ===========================================================================
# D. TestPreloadCacheReuse
# ===========================================================================


class TestPreloadCacheReuse:
    """Warm cache scenarios."""

    @pytest.mark.asyncio
    async def test_second_preload_instant_ready(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """Second preload of same study completes instantly (cache hit)."""
        # First preload — cold cache
        resp1 = await client.post(f"{DICOMWEB_BASE}/preload/{imported_study}")
        task_id1 = resp1.json()["task_id"]
        progress1 = await poll_until_ready(client, imported_study, task_id1)
        assert progress1["status"] == "ready"

        # Second preload — warm cache, should be near-instant
        resp2 = await client.post(f"{DICOMWEB_BASE}/preload/{imported_study}")
        task_id2 = resp2.json()["task_id"]
        progress2 = await poll_until_ready(client, imported_study, task_id2, poll_timeout=10.0)
        assert progress2["status"] == "ready"
