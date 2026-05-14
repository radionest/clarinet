"""E2E tests: DICOMweb proxy / OHIF Viewing Workflow.

Tests the complete DICOMweb proxy workflow that powers OHIF Viewer:
QIDO-RS study search, series search, instance search, WADO-RS metadata
retrieval, and pixel data frame retrieval.  Verifies the proxy correctly
translates DICOMweb requests into DICOM C-FIND/C-GET operations, caches
results, and returns valid DICOM JSON responses.

All tests auto-skip when Orthanc PACS is unreachable.

Run:
    uv run pytest tests/e2e/test_dicomweb_workflow.py -v
    uv run pytest -m dicom -v
"""

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
import requests
from httpx import ASGITransport, AsyncClient

from clarinet.api.app import app
from clarinet.api.dependencies import (
    get_dicom_client,
    get_dicomweb_cache,
    get_dicomweb_proxy_service,
    get_pacs_node,
)
from clarinet.services.dicom import DicomClient, DicomNode
from clarinet.services.dicomweb.cache import DicomWebCache
from clarinet.services.dicomweb.service import DicomWebProxyService
from clarinet.utils.database import get_async_session
from tests.config import CALLING_AET, PACS_AET, PACS_DICOM_PORT, PACS_HOST, PACS_REST_URL
from tests.utils.cookies import patch_cookie_forwarding
from tests.utils.urls import DICOMWEB_BASE

pytestmark = [pytest.mark.dicom]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACS_PORT = PACS_DICOM_PORT

DICOM_JSON_CT = "application/dicom+json"

# DICOM tag keywords used in DICOM JSON responses
TAG_STUDY_UID = "0020000D"
TAG_SERIES_UID = "0020000E"
TAG_SOP_INSTANCE_UID = "00080018"
TAG_SOP_CLASS_UID = "00080016"
TAG_PATIENT_ID = "00100020"
TAG_MODALITY = "00080060"
TAG_PIXEL_DATA = "7FE00010"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dicom_json_tag_value(obj: dict, tag: str) -> str | None:
    """Extract the first string value from a DICOM JSON tag entry."""
    entry = obj.get(tag)
    if not entry:
        return None
    values = entry.get("Value")
    if not values:
        return None
    val = values[0]
    # PN tags return nested dicts
    if isinstance(val, dict):
        return val.get("Alphabetic")
    return str(val)


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
        pytest.skip("Orthanc PACS server is not reachable — skipping DICOMweb tests")


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


@pytest.fixture(scope="session")
def pacs_series_uid(pacs_study_uid: str) -> str:
    """Fetch a real series UID within the known study from Orthanc REST API."""
    orthanc_ids = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {"StudyInstanceUID": pacs_study_uid}},
        timeout=5,
    ).json()
    assert orthanc_ids, f"Study {pacs_study_uid} not found on PACS"
    study_info = requests.get(f"{PACS_REST_URL}/studies/{orthanc_ids[0]}", timeout=5).json()
    series_ids = study_info["Series"]
    assert series_ids, f"Study {pacs_study_uid} has no series"
    series_info = requests.get(f"{PACS_REST_URL}/series/{series_ids[0]}", timeout=5).json()
    return series_info["MainDicomTags"]["SeriesInstanceUID"]


@pytest.fixture(scope="session")
def pacs_instance_uid(pacs_series_uid: str) -> str:
    """Fetch a real SOP Instance UID within the known series from Orthanc REST API."""
    orthanc_ids = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Series", "Query": {"SeriesInstanceUID": pacs_series_uid}},
        timeout=5,
    ).json()
    assert orthanc_ids, f"Series {pacs_series_uid} not found on PACS"
    series_info = requests.get(f"{PACS_REST_URL}/series/{orthanc_ids[0]}", timeout=5).json()
    instance_ids = series_info["Instances"]
    assert instance_ids, f"Series {pacs_series_uid} has no instances"
    instance_info = requests.get(f"{PACS_REST_URL}/instances/{instance_ids[0]}", timeout=5).json()
    return instance_info["MainDicomTags"]["SOPInstanceUID"]


@pytest_asyncio.fixture
async def client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Override e2e conftest's unauthenticated client with an authenticated one.

    DICOMweb endpoints require CurrentUserDep, so we need auth bypass.
    """
    from clarinet.api.auth_config import current_active_user, current_superuser
    from clarinet.models.user import User
    from clarinet.utils.auth import get_password_hash

    mock_user = User(
        id=uuid4(),
        email="e2e_dicomweb@test.com",
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
        from clarinet.settings import get_settings

        app.dependency_overrides[get_settings] = override_get_settings
    except (ImportError, AttributeError):
        pass

    try:
        import clarinet.api.auth_config

        clarinet.api.auth_config.settings = test_settings
    except (ImportError, AttributeError):
        pass

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
        patch_cookie_forwarding(ac)
        yield ac

    app.dependency_overrides.clear()


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

    # Shutdown cache to cancel background tasks
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


# ===========================================================================
# A. TestQidoRsStudySearch
# ===========================================================================


class TestQidoRsStudySearch:
    """QIDO-RS: Search for studies."""

    @pytest.mark.asyncio
    async def test_search_all_studies(
        self,
        client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """GET /dicom-web/studies returns DICOM JSON array of studies."""
        response = await client.get(f"{DICOMWEB_BASE}/studies")
        assert response.status_code == 200
        assert DICOM_JSON_CT in response.headers["content-type"]

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        for item in data:
            assert TAG_STUDY_UID in item, f"Missing StudyInstanceUID tag in {item.keys()}"
            uid = _dicom_json_tag_value(item, TAG_STUDY_UID)
            assert uid, "StudyInstanceUID value is empty"

    @pytest.mark.asyncio
    async def test_search_studies_with_patient_filter(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_patient_id: str,
    ) -> None:
        """GET /dicom-web/studies?PatientID=... filters results."""
        response = await client.get(
            f"{DICOMWEB_BASE}/studies", params={"PatientID": pacs_patient_id}
        )
        assert response.status_code == 200
        assert DICOM_JSON_CT in response.headers["content-type"]

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        for item in data:
            pid = _dicom_json_tag_value(item, TAG_PATIENT_ID)
            assert pid == pacs_patient_id, f"Expected PatientID {pacs_patient_id}, got {pid}"

    @pytest.mark.asyncio
    async def test_search_studies_empty_result(
        self,
        client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """GET /dicom-web/studies?PatientID=NONEXISTENT returns empty array."""
        response = await client.get(
            f"{DICOMWEB_BASE}/studies", params={"PatientID": "NONEXISTENT_99999"}
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_studies_requires_auth(
        self,
        unauthenticated_client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """GET /dicom-web/studies without auth returns 401."""
        response = await unauthenticated_client.get(f"{DICOMWEB_BASE}/studies")
        assert response.status_code == 401


# ===========================================================================
# B. TestQidoRsSeriesSearch
# ===========================================================================


class TestQidoRsSeriesSearch:
    """QIDO-RS: Search for series within a study."""

    @pytest.mark.asyncio
    async def test_search_series_in_study(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
    ) -> None:
        """GET /dicom-web/studies/{uid}/series returns series list."""
        response = await client.get(f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series")
        assert response.status_code == 200
        assert DICOM_JSON_CT in response.headers["content-type"]

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        for item in data:
            assert TAG_SERIES_UID in item, "Missing SeriesInstanceUID tag"
            series_uid = _dicom_json_tag_value(item, TAG_SERIES_UID)
            assert series_uid, "SeriesInstanceUID value is empty"

    @pytest.mark.asyncio
    async def test_search_series_with_modality_filter(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
    ) -> None:
        """GET /dicom-web/studies/{uid}/series?Modality=CT filters by modality."""
        response = await client.get(
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series", params={"Modality": "CT"}
        )
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        # All returned series should have Modality=CT
        for item in data:
            modality = _dicom_json_tag_value(item, TAG_MODALITY)
            assert modality == "CT", f"Expected Modality CT, got {modality}"

    @pytest.mark.asyncio
    async def test_search_series_nonexistent_study(
        self,
        client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """GET /dicom-web/studies/1.2.999.999/series returns empty array."""
        response = await client.get(f"{DICOMWEB_BASE}/studies/1.2.999.999/series")
        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# C. TestQidoRsInstanceSearch
# ===========================================================================


class TestQidoRsInstanceSearch:
    """QIDO-RS: Search for instances within a series."""

    @pytest.mark.asyncio
    async def test_search_instances_in_series(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> None:
        """GET .../instances returns instances with SOPInstanceUID."""
        response = await client.get(
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/{pacs_series_uid}/instances"
        )
        assert response.status_code == 200
        assert DICOM_JSON_CT in response.headers["content-type"]

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        for item in data:
            assert TAG_SOP_INSTANCE_UID in item, "Missing SOPInstanceUID tag"
            sop_uid = _dicom_json_tag_value(item, TAG_SOP_INSTANCE_UID)
            assert sop_uid, "SOPInstanceUID value is empty"

    @pytest.mark.asyncio
    async def test_search_instances_nonexistent_series(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
    ) -> None:
        """GET .../instances for nonexistent series returns empty array."""
        response = await client.get(
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/1.2.999.999/instances"
        )
        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# D. TestWadoRsMetadata
# ===========================================================================


class TestWadoRsMetadata:
    """WADO-RS: Retrieve series/study metadata."""

    @pytest.mark.asyncio
    async def test_retrieve_study_metadata(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
    ) -> None:
        """GET /dicom-web/studies/{uid}/metadata returns instance metadata."""
        response = await client.get(f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/metadata")
        assert response.status_code == 200
        assert DICOM_JSON_CT in response.headers["content-type"]

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        for item in data:
            assert TAG_SOP_INSTANCE_UID in item, "Missing SOPInstanceUID in metadata"
            assert TAG_SOP_CLASS_UID in item, "Missing SOPClassUID in metadata"

    @pytest.mark.asyncio
    async def test_retrieve_series_metadata(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> None:
        """GET .../series/{uid}/metadata returns DICOM JSON with BulkDataURIs."""
        response = await client.get(
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/{pacs_series_uid}/metadata"
        )
        assert response.status_code == 200
        assert DICOM_JSON_CT in response.headers["content-type"]

        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        for item in data:
            assert TAG_SOP_INSTANCE_UID in item
            assert TAG_SOP_CLASS_UID in item

            # BulkDataURIs should be present for pixel data tags
            pixel_tag = item.get(TAG_PIXEL_DATA)
            if pixel_tag:
                bulk_uri = pixel_tag.get("BulkDataURI")
                assert bulk_uri, "PixelData tag present but no BulkDataURI"
                assert "/frames/" in bulk_uri, f"BulkDataURI missing /frames/ path: {bulk_uri}"

    @pytest.mark.asyncio
    async def test_metadata_idempotent_cache(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> None:
        """Two metadata calls return identical results (cache hit on second)."""
        url = f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/{pacs_series_uid}/metadata"

        resp1 = await client.get(url)
        assert resp1.status_code == 200
        data1 = resp1.json()

        resp2 = await client.get(url)
        assert resp2.status_code == 200
        data2 = resp2.json()

        assert data1 == data2, "Second metadata call returned different result"

    @pytest.mark.asyncio
    async def test_metadata_nonexistent_study(
        self,
        client: AsyncClient,
        pacs_available: None,
    ) -> None:
        """GET .../studies/1.2.999.999/metadata returns empty array."""
        response = await client.get(f"{DICOMWEB_BASE}/studies/1.2.999.999/metadata")
        assert response.status_code == 200
        assert response.json() == []


# ===========================================================================
# E. TestWadoRsFrameRetrieval
# ===========================================================================


class TestWadoRsFrameRetrieval:
    """WADO-RS: Retrieve pixel data frames."""

    @pytest.mark.asyncio
    async def test_retrieve_single_frame(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        pacs_series_uid: str,
        pacs_instance_uid: str,
    ) -> None:
        """GET .../frames/1 returns pixel data after metadata populates cache."""
        # First, populate cache via metadata retrieval
        meta_resp = await client.get(
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/{pacs_series_uid}/metadata"
        )
        assert meta_resp.status_code == 200

        # Now retrieve frame 1
        frame_url = (
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}"
            f"/series/{pacs_series_uid}"
            f"/instances/{pacs_instance_uid}/frames/1"
        )
        response = await client.get(frame_url)
        assert response.status_code == 200

        ct = response.headers["content-type"]
        assert "multipart/related" in ct or "application/octet-stream" in ct
        assert len(response.content) > 0, "Frame response body is empty"

    @pytest.mark.asyncio
    async def test_retrieve_frame_nonexistent_instance(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> None:
        """GET .../frames/1 for nonexistent instance returns 404."""
        # Populate the series cache first
        meta_resp = await client.get(
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/{pacs_series_uid}/metadata"
        )
        assert meta_resp.status_code == 200

        frame_url = (
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}"
            f"/series/{pacs_series_uid}"
            f"/instances/1.2.999.999.999/frames/1"
        )
        response = await client.get(frame_url)
        assert response.status_code == 404


# ===========================================================================
# F. TestDicomWebAuthEnforcement
# ===========================================================================


class TestDicomWebAuthEnforcement:
    """All DICOMweb endpoints require authentication."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "path",
        [
            "/studies",
            "/studies/1.2.3/metadata",
            "/studies/1.2.3/series",
            "/studies/1.2.3/series/1.2.4/instances",
            "/studies/1.2.3/series/1.2.4/metadata",
            "/studies/1.2.3/series/1.2.4/instances/1.2.5/frames/1",
        ],
    )
    async def test_endpoint_requires_auth(
        self,
        unauthenticated_client: AsyncClient,
        path: str,
    ) -> None:
        """Each DICOMweb endpoint returns 401 without authentication."""
        response = await unauthenticated_client.get(f"{DICOMWEB_BASE}{path}")
        assert response.status_code == 401, f"{path} returned {response.status_code}, expected 401"


# ===========================================================================
# G. TestDicomWebCacheLifecycle
# ===========================================================================


class TestDicomWebCacheLifecycle:
    """Verify cache population and reuse behavior."""

    @pytest.mark.asyncio
    async def test_cache_populated_by_metadata_retrieval(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        pacs_series_uid: str,
        tmp_path: Path,
    ) -> None:
        """Metadata retrieval populates the cache (disk write in background)."""
        response = await client.get(
            f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/{pacs_series_uid}/metadata"
        )
        assert response.status_code == 200

        data = response.json()
        assert len(data) >= 1, "Metadata should contain at least one instance"

    @pytest.mark.asyncio
    async def test_cache_serves_subsequent_requests(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> None:
        """Subsequent metadata requests are served from cache with identical content."""
        url = f"{DICOMWEB_BASE}/studies/{pacs_study_uid}/series/{pacs_series_uid}/metadata"

        resp1 = await client.get(url)
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert len(data1) >= 1

        # Second request should hit cache
        resp2 = await client.get(url)
        assert resp2.status_code == 200
        data2 = resp2.json()

        assert len(data1) == len(data2), "Cache returned different number of instances"
        # Compare SOPInstanceUIDs to ensure same data
        uids1 = {_dicom_json_tag_value(i, TAG_SOP_INSTANCE_UID) for i in data1}
        uids2 = {_dicom_json_tag_value(i, TAG_SOP_INSTANCE_UID) for i in data2}
        assert uids1 == uids2, "Cache returned different instances"


# ===========================================================================
# H. TestOhifViewerIntegration
# ===========================================================================


class TestOhifViewerIntegration:
    """Simulates the OHIF Viewer's DICOMweb request sequence."""

    @pytest.mark.asyncio
    async def test_full_ohif_viewing_sequence(
        self,
        client: AsyncClient,
        pacs_available: None,
        pacs_study_uid: str,
    ) -> None:
        """Full OHIF viewing sequence: list studies → series → instances → metadata → frames."""
        # 1. QIDO-RS: Search studies
        resp = await client.get(f"{DICOMWEB_BASE}/studies")
        assert resp.status_code == 200
        studies = resp.json()
        assert len(studies) >= 1

        # Find our known study
        study_uid = pacs_study_uid
        matching = [s for s in studies if _dicom_json_tag_value(s, TAG_STUDY_UID) == study_uid]
        assert matching, f"Known study {study_uid} not found in QIDO-RS results"

        # 2. QIDO-RS: List series in study
        resp = await client.get(f"{DICOMWEB_BASE}/studies/{study_uid}/series")
        assert resp.status_code == 200
        series_list = resp.json()
        assert len(series_list) >= 1

        series_uid = _dicom_json_tag_value(series_list[0], TAG_SERIES_UID)
        assert series_uid

        # 3. QIDO-RS: List instances in series
        resp = await client.get(
            f"{DICOMWEB_BASE}/studies/{study_uid}/series/{series_uid}/instances"
        )
        assert resp.status_code == 200
        instances = resp.json()
        assert len(instances) >= 1

        instance_uid = _dicom_json_tag_value(instances[0], TAG_SOP_INSTANCE_UID)
        assert instance_uid

        # 4. WADO-RS: Get series metadata
        resp = await client.get(f"{DICOMWEB_BASE}/studies/{study_uid}/series/{series_uid}/metadata")
        assert resp.status_code == 200
        assert DICOM_JSON_CT in resp.headers["content-type"]
        metadata = resp.json()
        assert len(metadata) >= 1

        # 5. WADO-RS: Get pixel data for first instance
        frame_url = (
            f"{DICOMWEB_BASE}/studies/{study_uid}"
            f"/series/{series_uid}"
            f"/instances/{instance_uid}/frames/1"
        )
        resp = await client.get(frame_url)
        assert resp.status_code == 200
        assert len(resp.content) > 0, "Frame pixel data is empty"

        ct = resp.headers["content-type"]
        assert "multipart/related" in ct or "application/octet-stream" in ct
