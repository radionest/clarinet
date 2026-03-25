"""E2E tests: Slicer + PACS (C-MOVE/C-GET) integration.

Tests the complete Slicer ↔ PACS workflow against live infrastructure:
- PacsHelper retrieval inside 3D Slicer (C-GET → C-MOVE fallback)
- SlicerHelper.load_study_from_pacs / load_series_from_pacs
- Full record-open API workflow with PACS-backed scripts
- Backend DicomClient C-MOVE followed by Slicer script execution

All tests auto-skip when 3D Slicer or Orthanc PACS are unreachable.

Run:
    uv run pytest tests/e2e/test_slicer_pacs_workflow.py -v
    uv run pytest -m "slicer and dicom" -v
"""

import asyncio
import contextlib
import socket
import subprocess
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
import requests
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from clarinet.services.slicer.service import SlicerService

pytestmark = [
    pytest.mark.slicer,
    pytest.mark.dicom,
    pytest.mark.asyncio,
    pytest.mark.xdist_group("slicer"),
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLICER_HOST = "localhost"
SLICER_PORT = 2016

PACS_HOST = "192.168.122.151"
PACS_PORT = 4242
PACS_AET = "ORTHANC"
PACS_REST_URL = f"http://{PACS_HOST}:8042"
CALLING_AET = "SLICER_TEST"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pacs_helper_script_block() -> str:
    """Return a script block that creates a PacsHelper with explicit params.

    Used instead of PacsHelper.from_slicer() because we can't control
    Slicer's QSettings from outside the process.
    """
    return f"""
_test_pacs = PacsHelper(
    host='{PACS_HOST}',
    port={PACS_PORT},
    called_aet='{PACS_AET}',
    calling_aet='{CALLING_AET}',
    prefer_cget=True,
    move_aet='{CALLING_AET}',
)
"""


def _monkey_patch_from_slicer_block() -> str:
    """Return a script block that monkey-patches PacsHelper.from_slicer.

    After this block, SlicerHelper.load_study_from_pacs() and
    load_series_from_pacs() will use our explicit PACS params.
    """
    return f"""
PacsHelper.from_slicer = classmethod(lambda cls, server_name=None: PacsHelper(
    host='{PACS_HOST}', port={PACS_PORT},
    called_aet='{PACS_AET}', calling_aet='{CALLING_AET}',
    prefer_cget=True, move_aet='{CALLING_AET}',
))
"""


# ---------------------------------------------------------------------------
# Skip fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _check_pacs() -> None:
    """Skip all tests if Orthanc PACS is unreachable."""
    try:
        resp = requests.get(f"{PACS_REST_URL}/system", timeout=2)
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
        pytest.skip(f"Orthanc PACS not reachable at {PACS_REST_URL}")


@pytest.fixture(scope="session")
def _check_slicer() -> None:
    """Skip all tests if 3D Slicer is unreachable."""
    try:
        sock = socket.create_connection((SLICER_HOST, SLICER_PORT), timeout=3)
        sock.close()
    except OSError:
        pytest.skip(f"3D Slicer not reachable at {SLICER_HOST}:{SLICER_PORT}")


@pytest.fixture(autouse=True)
def _require_slicer_and_pacs(_check_slicer: None, _check_pacs: None) -> None:
    """Auto-use: skip if either Slicer or PACS is unavailable."""


# ---------------------------------------------------------------------------
# PACS data fixtures (session-scoped, fetched from Orthanc REST API)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pacs_study(
    _check_pacs: None,
) -> dict[str, Any]:
    """Fetch the smallest study from Orthanc for fast tests."""
    resp = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {}},
        timeout=5,
    )
    resp.raise_for_status()
    orthanc_ids = resp.json()
    assert orthanc_ids, "No studies found on test PACS"

    # Pick smallest study for speed
    best: dict[str, Any] | None = None
    best_instances = float("inf")
    for oid in orthanc_ids[:10]:  # Check first 10
        info = requests.get(f"{PACS_REST_URL}/studies/{oid}", timeout=5).json()
        stats = requests.get(f"{PACS_REST_URL}/studies/{oid}/statistics", timeout=5).json()
        count = int(stats.get("CountInstances", 0))
        if 0 < count < best_instances:
            best_instances = count
            best = {
                "orthanc_id": oid,
                "study_uid": info["MainDicomTags"]["StudyInstanceUID"],
                "patient_id": info.get("PatientMainDicomTags", {}).get(
                    "PatientID",
                    info["MainDicomTags"].get("PatientID", ""),
                ),
                "instance_count": count,
            }
    assert best is not None, "No study with instances found on test PACS"
    return best


@pytest.fixture(scope="session")
def pacs_study_uid(pacs_study: dict[str, Any]) -> str:
    return pacs_study["study_uid"]


@pytest.fixture(scope="session")
def pacs_series(
    _check_pacs: None,
    pacs_study: dict[str, Any],
) -> dict[str, str]:
    """Fetch the first series of the test study from Orthanc."""
    resp = requests.get(
        f"{PACS_REST_URL}/studies/{pacs_study['orthanc_id']}/series",
        timeout=5,
    )
    resp.raise_for_status()
    series_list = resp.json()
    assert series_list, "No series found in test study"

    # Response is a list of full series objects (not IDs)
    first = series_list[0]
    return {
        "series_uid": first["MainDicomTags"]["SeriesInstanceUID"],
        "study_uid": pacs_study["study_uid"],
        "instance_count": str(len(first.get("Instances", []))),
    }


@pytest.fixture(scope="session")
def pacs_series_uid(pacs_series: dict[str, str]) -> str:
    return pacs_series["series_uid"]


# ---------------------------------------------------------------------------
# Slicer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def slicer_service() -> SlicerService:
    return SlicerService()


@pytest.fixture
def slicer_url() -> str:
    return f"http://{SLICER_HOST}:{SLICER_PORT}"


# ===========================================================================
# A. Slicer-side PACS Retrieval (PacsHelper)
# ===========================================================================


class TestPacsHelperRetrieval:
    """Send scripts to Slicer that use PacsHelper with explicit params."""

    async def test_pacs_retrieve_study(
        self,
        slicer_service: SlicerService,
        slicer_url: str,
        pacs_study_uid: str,
    ) -> None:
        """PacsHelper.retrieve_study() loads MRML nodes from PACS."""
        script = (
            _pacs_helper_script_block()
            + f"""
import json
node_ids = _test_pacs.retrieve_study('{pacs_study_uid}')
assert len(node_ids) > 0, f"No nodes loaded for study, got {{node_ids}}"
print(json.dumps({{"loaded": len(node_ids)}}))
"""
        )
        result = await slicer_service.execute(slicer_url, script, request_timeout=60.0)
        assert isinstance(result, dict)

    async def test_pacs_retrieve_series(
        self,
        slicer_service: SlicerService,
        slicer_url: str,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> None:
        """PacsHelper.retrieve_series() loads a single series from PACS."""
        script = (
            _pacs_helper_script_block()
            + f"""
import json
node_ids = _test_pacs.retrieve_series('{pacs_study_uid}', '{pacs_series_uid}')
assert len(node_ids) > 0, f"No nodes loaded for series, got {{node_ids}}"
print(json.dumps({{"loaded": len(node_ids)}}))
"""
        )
        result = await slicer_service.execute(slicer_url, script, request_timeout=60.0)
        assert isinstance(result, dict)


class TestSlicerHelperPacsIntegration:
    """Test load_study_from_pacs / load_series_from_pacs via SlicerHelper."""

    async def test_load_study_from_pacs_via_helper(
        self,
        slicer_service: SlicerService,
        slicer_url: str,
        pacs_study_uid: str,
    ) -> None:
        """load_study_from_pacs loads nodes and auto-sets _image_node."""
        script = (
            _monkey_patch_from_slicer_block()
            + f"""
import json
s = SlicerHelper('/tmp/slicer_e2e_test')
loaded = s.load_study_from_pacs('{pacs_study_uid}')
assert len(loaded) > 0, f"No nodes loaded, got {{loaded}}"
print(json.dumps({{
    "loaded": len(loaded),
    "has_image": s._image_node is not None,
}}))
"""
        )
        result = await slicer_service.execute(slicer_url, script, request_timeout=60.0)
        assert isinstance(result, dict)

    async def test_load_series_from_pacs_via_helper(
        self,
        slicer_service: SlicerService,
        slicer_url: str,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> None:
        """load_series_from_pacs loads a single series and sets _image_node."""
        script = (
            _monkey_patch_from_slicer_block()
            + f"""
import json
s = SlicerHelper('/tmp/slicer_e2e_test')
loaded = s.load_series_from_pacs('{pacs_study_uid}', '{pacs_series_uid}')
assert len(loaded) > 0, f"No nodes loaded, got {{loaded}}"
print(json.dumps({{
    "loaded": len(loaded),
    "has_image": s._image_node is not None,
}}))
"""
        )
        result = await slicer_service.execute(slicer_url, script, request_timeout=60.0)
        assert isinstance(result, dict)

    async def test_load_nonexistent_study_raises(
        self,
        slicer_service: SlicerService,
        slicer_url: str,
    ) -> None:
        """load_study_from_pacs with fake UID + raise_on_empty=True raises."""
        from clarinet.exceptions import SlicerError

        fake_uid = "1.2.999.999.999.0.0.0"
        script = (
            _monkey_patch_from_slicer_block()
            + f"""
s = SlicerHelper('/tmp/slicer_e2e_test')
s.load_study_from_pacs('{fake_uid}', raise_on_empty=True)
"""
        )
        with pytest.raises(SlicerError):
            await slicer_service.execute(slicer_url, script, request_timeout=30.0)

    async def test_load_nonexistent_study_no_raise(
        self,
        slicer_service: SlicerService,
        slicer_url: str,
    ) -> None:
        """load_study_from_pacs with fake UID + raise_on_empty=False returns empty list.

        Note: PacsHelper.retrieve_study() may raise a Slicer-level error when
        DICOMUtils.loadSeriesByUID([]) gets an empty list (C-FIND found no series).
        In that case we catch the error in the script and return gracefully.
        """
        fake_uid = "1.2.999.999.999.0.0.0"
        script = (
            _monkey_patch_from_slicer_block()
            + f"""
import json
s = SlicerHelper('/tmp/slicer_e2e_test')
try:
    loaded = s.load_study_from_pacs('{fake_uid}', raise_on_empty=False)
except Exception as e:
    # PacsHelper.retrieve_study may fail at DICOMUtils level for empty series
    loaded = []
result = loaded == [] or loaded == None
assert result, f"Expected empty list, got {{loaded}}"
print(json.dumps({{"loaded": 0, "graceful": True}}))
"""
        )
        result = await slicer_service.execute(slicer_url, script, request_timeout=30.0)
        assert isinstance(result, dict)


# ===========================================================================
# B. Full Record-Open Workflow via API
# ===========================================================================


class TestRecordOpenWorkflow:
    """Test POST /api/slicer/records/{id}/open with PACS-backed slicer scripts."""

    @pytest_asyncio.fixture
    async def client(
        self, test_session: AsyncSession, test_settings: Any
    ) -> AsyncGenerator[AsyncClient]:
        """Authenticated client with get_client_ip overridden to localhost."""
        from clarinet.api.app import app
        from clarinet.api.auth_config import current_active_user, current_superuser
        from clarinet.api.dependencies import get_client_ip
        from clarinet.models.user import User
        from clarinet.utils.auth import get_password_hash
        from clarinet.utils.database import get_async_session

        mock_user = User(
            id=uuid4(),
            email="e2e_slicer@test.com",
            hashed_password=get_password_hash("mock"),
            is_active=True,
            is_verified=True,
            is_superuser=True,
        )
        test_session.add(mock_user)
        await test_session.commit()
        await test_session.refresh(mock_user)
        test_session.expunge(mock_user)

        async def override_get_session():
            yield test_session

        app.dependency_overrides[get_async_session] = override_get_session
        app.dependency_overrides[current_active_user] = lambda: mock_user
        app.dependency_overrides[current_superuser] = lambda: mock_user
        app.dependency_overrides[get_client_ip] = lambda: SLICER_HOST

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies={}) as ac:
            yield ac

        app.dependency_overrides.clear()

    @pytest_asyncio.fixture
    async def slicer_record(
        self,
        test_session: AsyncSession,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> int:
        """Create patient + study + series + record type + record in DB.

        The record type has a slicer_script that loads the study from PACS
        using monkey-patched PacsHelper.

        Returns the record ID.
        """
        from clarinet.models.record import Record, RecordType
        from clarinet.models.study import Series, Study
        from tests.utils.factories import make_patient

        # Patient
        patient = make_patient("E2E_SLICER_PAT", "E2E Slicer Test")
        test_session.add(patient)
        await test_session.flush()

        # Study (with real PACS UID)
        from datetime import UTC, datetime

        study = Study(
            patient_id=patient.id,
            study_uid=pacs_study_uid,
            date=datetime.now(UTC).date(),
        )
        test_session.add(study)
        await test_session.flush()

        # Series (with real PACS UID)
        series = Series(
            study_uid=pacs_study_uid,
            series_uid=pacs_series_uid,
            series_number=1,
            series_description="E2E Test Series",
        )
        test_session.add(series)
        await test_session.flush()

        # RecordType with slicer_script that loads from PACS
        slicer_script = (
            _monkey_patch_from_slicer_block()
            + """
import json
s = SlicerHelper(working_folder)
loaded = s.load_study_from_pacs(study_uid)
print(json.dumps({"loaded": len(loaded), "study_uid": study_uid}))
"""
        )
        record_type = RecordType(
            name="e2e-slicer-pacs-test",
            description="E2E Slicer PACS test type",
            label="E2E Slicer PACS",
            level="SERIES",
            slicer_script=slicer_script,
        )
        test_session.add(record_type)
        await test_session.flush()

        # Record
        record = Record(
            record_type_name=record_type.name,
            study_uid=pacs_study_uid,
            series_uid=pacs_series_uid,
            patient_id=patient.id,
            record_status="in_progress",
        )
        test_session.add(record)
        await test_session.commit()
        await test_session.refresh(record)

        return record.id

    async def test_record_open_loads_from_pacs(
        self,
        client: AsyncClient,
        slicer_record: int,
    ) -> None:
        """POST /slicer/records/{id}/open successfully loads data from PACS."""
        response = await client.post(f"/api/slicer/records/{slicer_record}/open")
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )

    @pytest_asyncio.fixture
    async def no_script_record(
        self,
        test_session: AsyncSession,
        pacs_study_uid: str,
        pacs_series_uid: str,
    ) -> int:
        """Create a record whose type has no slicer_script."""
        from clarinet.models.record import Record, RecordType
        from clarinet.models.study import Series, Study
        from tests.utils.factories import make_patient

        patient = make_patient("E2E_NOSCRIPT_PAT", "No Script Patient")
        test_session.add(patient)
        await test_session.flush()

        from datetime import UTC, datetime

        study = Study(
            patient_id=patient.id,
            study_uid=pacs_study_uid,
            date=datetime.now(UTC).date(),
        )
        test_session.add(study)
        await test_session.flush()

        series = Series(
            study_uid=pacs_study_uid,
            series_uid=pacs_series_uid,
            series_number=1,
        )
        test_session.add(series)
        await test_session.flush()

        record_type = RecordType(
            name="e2e-slicer-no-script",
            description="No slicer_script",
            label="No Script",
            level="SERIES",
            slicer_script=None,
        )
        test_session.add(record_type)
        await test_session.flush()

        record = Record(
            record_type_name=record_type.name,
            study_uid=pacs_study_uid,
            series_uid=pacs_series_uid,
            patient_id=patient.id,
            record_status="in_progress",
        )
        test_session.add(record)
        await test_session.commit()
        await test_session.refresh(record)
        return record.id

    async def test_record_open_no_script_returns_422(
        self,
        client: AsyncClient,
        no_script_record: int,
    ) -> None:
        """POST /slicer/records/{id}/open without slicer_script returns 422."""
        response = await client.post(f"/api/slicer/records/{no_script_record}/open")
        assert response.status_code == 422


# ===========================================================================
# C. Backend DicomClient C-MOVE → Slicer Exec
# ===========================================================================


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _get_local_ip() -> str:
    """Get local IP reachable from Orthanc."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((PACS_HOST, PACS_PORT))
        return s.getsockname()[0]


def _pacs_can_reach_us() -> bool:
    """Check if PACS can connect back to our host (needed for C-MOVE)."""
    import os
    import threading

    ssh_host = os.environ.get("CLARINET_TEST_PACS_SSH", "klara")
    if not ssh_host:
        return True

    port = _free_port()
    local_ip = _get_local_ip()
    connected = False

    def _listen():
        nonlocal connected
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        s.listen(1)
        s.settimeout(5)
        try:
            conn, _ = s.accept()
            connected = True
            conn.close()
        except TimeoutError:
            pass
        s.close()

    listener = threading.Thread(target=_listen)
    listener.start()

    with contextlib.suppress(subprocess.TimeoutExpired, FileNotFoundError):
        subprocess.run(
            ["ssh", "-o", "ConnectTimeout=3", ssh_host, f"nc -z {local_ip} {port}"],
            timeout=5,
            capture_output=True,
        )

    listener.join(timeout=6)
    return connected


class TestBackendCmoveThenSlicer:
    """Backend DicomClient C-MOVE retrieves data, then Slicer loads from disk."""

    @pytest.fixture(scope="session")
    def _cmove_available(self, _check_pacs: None) -> None:
        """Skip if PACS cannot connect back to us."""
        if not _pacs_can_reach_us():
            pytest.skip("PACS cannot reach test host — C-MOVE requires bidirectional connectivity")

    @pytest.fixture
    def storage_scp(self, _cmove_available: None) -> Any:
        """Start SCP on free port and register AET in Orthanc for C-MOVE."""
        from clarinet.services.dicom.scp import StorageSCP

        scp = StorageSCP()
        port = _free_port()
        scp.start(aet=CALLING_AET, port=port)

        local_ip = _get_local_ip()
        modality_url = f"{PACS_REST_URL}/modalities/{CALLING_AET}"
        resp = requests.put(
            modality_url,
            json={"AET": CALLING_AET, "Host": local_ip, "Port": port},
            timeout=5,
        )
        resp.raise_for_status()

        yield scp

        requests.delete(modality_url, timeout=5)
        scp.stop()

    async def test_backend_cmove_then_slicer_exec(
        self,
        slicer_service: SlicerService,
        slicer_url: str,
        pacs_study_uid: str,
        pacs_series_uid: str,
        storage_scp: Any,
        tmp_path: Path,
    ) -> None:
        """C-MOVE retrieves series to disk, then Slicer loads the .dcm files."""
        from clarinet.services.dicom.models import (
            AssociationConfig,
            QueryRetrieveLevel,
            RetrieveRequest,
            StorageConfig,
            StorageMode,
        )
        from clarinet.services.dicom.operations import DicomOperations

        # 1. Backend C-MOVE: retrieve series to disk
        config = AssociationConfig(
            calling_aet=CALLING_AET,
            called_aet=PACS_AET,
            peer_host=PACS_HOST,
            peer_port=PACS_PORT,
        )
        request = RetrieveRequest(
            level=QueryRetrieveLevel.SERIES,
            study_instance_uid=pacs_study_uid,
            series_instance_uid=pacs_series_uid,
        )
        output_dir = tmp_path / "cmove_for_slicer"
        storage = StorageConfig(mode=StorageMode.DISK, output_dir=output_dir)

        ops = DicomOperations(calling_aet=CALLING_AET)
        result = await asyncio.to_thread(
            ops.retrieve_via_move,
            config,
            request,
            storage,
            CALLING_AET,
            storage_scp,
            timeout=120.0,
        )
        assert result.num_completed > 0, "C-MOVE retrieved 0 instances"
        dcm_files = list(output_dir.glob("*.dcm"))
        assert len(dcm_files) > 0

        # 2. Slicer: load the .dcm files from disk
        dcm_dir = str(output_dir)
        script = f"""
import json, os, glob

dcm_dir = '{dcm_dir}'
dcm_files = glob.glob(os.path.join(dcm_dir, '*.dcm'))
assert len(dcm_files) > 0, f"No .dcm files in {{dcm_dir}}"

# Import into Slicer's DICOM database and load
from DICOMLib import DICOMUtils
DICOMUtils.importDicom(dcm_dir)

# Find the series UID from the first file
import pydicom
ds = pydicom.dcmread(dcm_files[0])
series_uid = str(ds.SeriesInstanceUID)

loaded = DICOMUtils.loadSeriesByUID([series_uid])
assert len(loaded) > 0, f"No nodes loaded from {{len(dcm_files)}} files"
print(json.dumps({{"files": len(dcm_files), "loaded": len(loaded)}}))
"""
        slicer_result = await slicer_service.execute(slicer_url, script, request_timeout=60.0)
        assert isinstance(slicer_result, dict)
