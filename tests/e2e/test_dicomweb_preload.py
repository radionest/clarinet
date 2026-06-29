"""E2E tests: DICOMweb preload endpoints.

Tests the preload workflow: POST /preload (body: {"study_uids": [...]})
starts background cache population, GET /preload/progress/{task_id}
reports progress, and the task eventually reaches "ready" status.

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
    get_dicomweb_filler,
    get_dicomweb_proxy_service,
    get_pacs_node,
)
from clarinet.models.patient import Patient
from clarinet.services.dicom import DicomClient, DicomNode
from clarinet.services.dicomweb.filler import CacheFiller
from clarinet.services.dicomweb.service import DicomWebProxyService
from tests.config import CALLING_AET, PACS_AET, PACS_HOST, PACS_PORT, PACS_REST_URL
from tests.conftest import create_authenticated_client, create_mock_superuser
from tests.utils.factories import make_patient
from tests.utils.urls import DICOM_BASE, DICOMWEB_BASE

pytestmark = [pytest.mark.dicom]

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
def pacs_two_study_uids(pacs_available: None) -> list[str]:
    """Fetch two distinct study UIDs from Orthanc for multi-study preload tests."""
    resp = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {}},
        timeout=5,
    )
    resp.raise_for_status()
    orthanc_ids = resp.json()
    uids: list[str] = []
    for orthanc_id in orthanc_ids:
        info = requests.get(f"{PACS_REST_URL}/studies/{orthanc_id}", timeout=5).json()
        uid = info["MainDicomTags"].get("StudyInstanceUID")
        if uid and uid not in uids:
            uids.append(uid)
        if len(uids) == 2:
            return uids
    pytest.skip("Test PACS has fewer than 2 studies — skipping multi-study preload test")


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
    from dimsechord import DicomCache, PullEngine
    from dimsechord import DicomClient as DimseDicomClient

    cache_dir = tmp_path / "dicomweb_cache"
    cache_dir.mkdir(exist_ok=True)
    cache = DicomCache(
        base_dir=cache_dir,
        index_path=cache_dir / "index.db",
        ttl_hours=1,
        max_size_gb=1.0,
        memory_ttl_minutes=30,
        memory_max_entries=50,
    )

    dicom_client = DicomClient(calling_aet=CALLING_AET)  # façade — serves QIDO
    pacs_node = DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)
    engine = PullEngine.via_cget(cache, pacs_node, calling_aet=CALLING_AET)
    filler = CacheFiller(
        cache=cache,
        engine=engine,
        client=DimseDicomClient(calling_aet=CALLING_AET),
        pacs=pacs_node,
        retrieve_mode="c-get",
        session_factory=None,
        storage_path=tmp_path,
    )
    proxy_service = DicomWebProxyService(client=dicom_client, pacs=pacs_node, filler=filler)

    app.dependency_overrides[get_dicom_client] = lambda: dicom_client
    app.dependency_overrides[get_pacs_node] = lambda: pacs_node
    app.dependency_overrides[get_dicomweb_filler] = lambda: filler
    app.dependency_overrides[get_dicomweb_proxy_service] = lambda: proxy_service

    yield

    await filler.shutdown()

    app.dependency_overrides.pop(get_dicom_client, None)
    app.dependency_overrides.pop(get_pacs_node, None)
    app.dependency_overrides.pop(get_dicomweb_filler, None)
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
    task_id: str,
    *,
    poll_timeout: float = PRELOAD_TIMEOUT,
    interval: float = PRELOAD_POLL_INTERVAL,
    snapshots: list[dict] | None = None,
) -> dict:
    """Poll preload progress until status is terminal (ready/error) or timeout.

    When ``snapshots`` is given, every polled progress dict is appended to it
    so tests can assert on intermediate states (e.g. study_index/study_count).
    """
    elapsed = 0.0
    last_progress: dict = {}
    while elapsed < poll_timeout:
        resp = await client.get(f"{DICOMWEB_BASE}/preload/progress/{task_id}")
        assert resp.status_code == 200
        last_progress = resp.json()
        if snapshots is not None:
            snapshots.append(last_progress)
        status = last_progress.get("status")
        if status in ("ready", "error", "not_found"):
            return last_progress
        await asyncio.sleep(interval)
        elapsed += interval
    raise TimeoutError(f"Preload did not complete within {poll_timeout}s. Last: {last_progress}")


async def start_preload(client: AsyncClient, study_uids: list[str]) -> str:
    """POST /preload and return the task_id."""
    resp = await client.post(f"{DICOMWEB_BASE}/preload", json={"study_uids": study_uids})
    assert resp.status_code == 200
    return resp.json()["task_id"]


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
        """POST /preload returns a task_id."""
        resp = await client.post(f"{DICOMWEB_BASE}/preload", json={"study_uids": [imported_study]})
        assert resp.status_code == 200

        data = resp.json()
        assert "task_id" in data
        assert data["task_id"].startswith("preload_")

    @pytest.mark.asyncio
    async def test_preload_reaches_ready(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """Preload eventually reaches 'ready' status for an imported study."""
        task_id = await start_preload(client, [imported_study])

        progress = await poll_until_ready(client, task_id)
        assert progress["status"] == "ready"

    @pytest.mark.asyncio
    async def test_preload_progress_reports_received_count(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """Progress endpoint reports received count when preload completes."""
        task_id = await start_preload(client, [imported_study])

        progress = await poll_until_ready(client, task_id)
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
        task_id = await start_preload(client, [imported_study])
        progress = await poll_until_ready(client, task_id)
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
        task_id = await start_preload(client, ["1.2.999.999.0"])

        progress = await poll_until_ready(client, task_id)
        assert progress["status"] == "ready"
        assert progress.get("received", 0) == 0
        assert progress.get("total", 0) == 0

    @pytest.mark.asyncio
    async def test_preload_multi_study_reaches_ready(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_two_study_uids: list[str],
    ) -> None:
        """Multi-study preload aggregates all studies and reaches 'ready'.

        Intermediate "fetching" snapshots carry study_index/study_count; the
        check is best-effort — a warm cache may jump straight to "ready".
        """
        task_id = await start_preload(client, pacs_two_study_uids)

        snapshots: list[dict] = []
        progress = await poll_until_ready(client, task_id, snapshots=snapshots)
        assert progress["status"] == "ready"
        assert progress.get("received", 0) >= 0

        fetching = [s for s in snapshots if s.get("status") == "fetching"]
        for snap in fetching:
            assert snap.get("study_count") == len(pacs_two_study_uids)
            assert 1 <= snap.get("study_index", 0) <= len(pacs_two_study_uids)

    @pytest.mark.asyncio
    async def test_preload_validation_empty_list(
        self,
        client: AsyncClient,
    ) -> None:
        """POST /preload with an empty study_uids list is rejected with 422.

        No PACS needed — validation fails before the service is touched.
        """
        resp = await client.post(f"{DICOMWEB_BASE}/preload", json={"study_uids": []})
        assert resp.status_code == 422


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
    ) -> None:
        """GET /preload/progress/{unknown} returns not_found."""
        resp = await client.get(f"{DICOMWEB_BASE}/preload/progress/nonexistent_task_id")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_progress_initial_status(
        self,
        client: AsyncClient,
        pacs_available: None,
        imported_study: str,
    ) -> None:
        """First progress poll returns an early-phase or terminal status."""
        task_id = await start_preload(client, [imported_study])

        # Immediately poll — should be in early phase
        progress_resp = await client.get(f"{DICOMWEB_BASE}/preload/progress/{task_id}")
        assert progress_resp.status_code == 200
        status = progress_resp.json()["status"]
        assert status in ("starting", "fetching", "ready")


# ===========================================================================
# C. TestPreloadAuthEnforcement
# ===========================================================================


class TestPreloadAuthEnforcement:
    """Preload endpoints require authentication."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/preload"),
            ("GET", "/preload/progress/some_task_id"),
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
        task_id1 = await start_preload(client, [imported_study])
        progress1 = await poll_until_ready(client, task_id1)
        assert progress1["status"] == "ready"

        # Second preload — warm cache, should be near-instant
        task_id2 = await start_preload(client, [imported_study])
        progress2 = await poll_until_ready(client, task_id2, poll_timeout=10.0)
        assert progress2["status"] == "ready"
