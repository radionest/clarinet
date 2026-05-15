"""Integration tests for C-MOVE self-retrieval against live Orthanc PACS.

These tests start a local Storage SCP on a free port, send C-MOVE to Orthanc,
and verify that instances arrive via the SCP.

Orthanc must be configured to allow C-MOVE to unknown AETs (default behavior)
or have our test AET registered in its DicomModalities config.

Run:
    uv run pytest tests/integration/test_dicom_cmove.py -v
    uv run pytest -m dicom -v
"""

import asyncio
import contextlib
import os
import socket
import subprocess
from pathlib import Path

import pytest
import requests

from clarinet.services.dicom import DicomClient, DicomNode, SeriesQuery, StudyResult
from clarinet.services.dicom.models import SeriesResult
from clarinet.services.dicom.scp import StorageSCP
from tests.config import PACS_AET, PACS_HOST, PACS_PORT, PACS_REST_URL

# ---------------------------------------------------------------------------
# Constants (same Orthanc as test_dicom_service.py)
# ---------------------------------------------------------------------------

# Per-worker AET so xdist workers don't clobber each other's modality
# registration in Orthanc (which is keyed by AET name); a shared name
# caused C-MOVE responses for one worker to flow into another worker's
# SCP, doubling its received_count. AET length limit is 16 chars.
CALLING_AET = f"CMOVE_T_{os.environ.get('PYTEST_XDIST_WORKER', 'master')}"


def _free_port() -> int:
    """Find a free TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pacs_available() -> None:
    """Skip all tests if Orthanc is unreachable."""
    try:
        resp = requests.get(f"{PACS_REST_URL}/system", timeout=2)
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
        pytest.skip("Orthanc PACS not reachable — skipping C-MOVE tests")


@pytest.fixture(scope="session")
def orthanc_node(pacs_available: None) -> DicomNode:
    return DicomNode(aet=PACS_AET, host=PACS_HOST, port=PACS_PORT)


@pytest.fixture(scope="session")
def dicom_client() -> DicomClient:
    return DicomClient(calling_aet=CALLING_AET)


@pytest.fixture(scope="session")
def small_mr_study(dicom_client: DicomClient, orthanc_node: DicomNode) -> StudyResult:
    """Find the smallest MR study on Orthanc for fast C-MOVE tests."""
    from clarinet.services.dicom import StudyQuery

    studies = asyncio.run(dicom_client.find_studies(StudyQuery(), orthanc_node))
    mr = [s for s in studies if s.modalities_in_study and "MR" in s.modalities_in_study]
    assert mr, "No MR study found on test PACS"
    return min(mr, key=lambda s: s.number_of_study_related_instances or float("inf"))


@pytest.fixture(scope="session")
def mr_series(
    dicom_client: DicomClient, orthanc_node: DicomNode, small_mr_study: StudyResult
) -> SeriesResult:
    """First series of the small MR study."""
    series_list = asyncio.run(
        dicom_client.find_series(
            SeriesQuery(study_instance_uid=small_mr_study.study_instance_uid),
            orthanc_node,
        )
    )
    assert series_list, "No series found in MR study"
    return series_list[0]


def _get_local_ip() -> str:
    """Get local IP reachable from Orthanc (same subnet)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect((PACS_HOST, PACS_PORT))
        return s.getsockname()[0]


def _pacs_can_reach_us() -> bool:
    """Check if PACS can connect back to our host (needed for C-MOVE).

    Uses SSH to ask the PACS host to ``nc`` our listener port.
    The SSH host is configurable via ``CLARINET_TEST_PACS_SSH``
    (default: ``klara``).  Set to empty string to skip the check
    and assume connectivity.
    """
    import os
    import threading

    ssh_host = os.environ.get("CLARINET_TEST_PACS_SSH", "klara")
    if not ssh_host:
        return True  # Assume reachable when env var is explicitly empty

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


@pytest.fixture(scope="session")
def cmove_available(pacs_available: None) -> None:
    """Skip if PACS cannot connect back to us (firewall, NAT, etc.)."""
    if not _pacs_can_reach_us():
        pytest.skip(
            "PACS cannot connect back to test host — "
            "C-MOVE tests require bidirectional connectivity"
        )


@pytest.fixture
def storage_scp(cmove_available: None):
    """Start SCP on free port and register AET in Orthanc for C-MOVE."""
    scp = StorageSCP()
    port = _free_port()
    scp.start(aet=CALLING_AET, port=port)

    # Register our AET in Orthanc so C-MOVE knows where to send
    local_ip = _get_local_ip()
    modality_url = f"{PACS_REST_URL}/modalities/{CALLING_AET}"
    resp = requests.put(
        modality_url,
        json={"AET": CALLING_AET, "Host": local_ip, "Port": port},
        timeout=5,
    )
    resp.raise_for_status()

    yield scp

    # Cleanup: remove modality and stop SCP
    requests.delete(modality_url, timeout=5)
    scp.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_cmove_series_to_memory(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    small_mr_study: StudyResult,
    mr_series: SeriesResult,
    storage_scp: StorageSCP,
) -> None:
    """C-MOVE retrieves a series and SCP collects instances in memory."""
    from clarinet.services.dicom.models import (
        AssociationConfig,
        QueryRetrieveLevel,
        RetrieveRequest,
        StorageConfig,
        StorageMode,
    )

    config = AssociationConfig(
        calling_aet=CALLING_AET,
        called_aet=PACS_AET,
        peer_host=PACS_HOST,
        peer_port=PACS_PORT,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=small_mr_study.study_instance_uid,
        series_instance_uid=mr_series.series_instance_uid,
    )
    storage = StorageConfig(mode=StorageMode.MEMORY)

    from clarinet.services.dicom.operations import DicomOperations

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

    assert result.instances, "No instances received via C-MOVE"
    assert result.num_completed > 0
    # Cross-check: instance count should match series instance count from C-FIND
    if mr_series.number_of_series_related_instances:
        assert len(result.instances) == mr_series.number_of_series_related_instances


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_cmove_study_to_disk(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    small_mr_study: StudyResult,
    storage_scp: StorageSCP,
    tmp_path: Path,
) -> None:
    """C-MOVE retrieves a study and writes .dcm files to disk."""
    from clarinet.services.dicom.models import (
        AssociationConfig,
        QueryRetrieveLevel,
        RetrieveRequest,
        StorageConfig,
        StorageMode,
    )

    config = AssociationConfig(
        calling_aet=CALLING_AET,
        called_aet=PACS_AET,
        peer_host=PACS_HOST,
        peer_port=PACS_PORT,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.STUDY,
        study_instance_uid=small_mr_study.study_instance_uid,
    )
    output_dir = tmp_path / "cmove_study"
    storage = StorageConfig(mode=StorageMode.DISK, output_dir=output_dir)

    from clarinet.services.dicom.operations import DicomOperations

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

    assert result.num_completed > 0
    dcm_files = list(output_dir.glob("*.dcm"))
    assert len(dcm_files) == result.num_completed


@pytest.mark.dicom
@pytest.mark.asyncio
async def test_cmove_matches_cget(
    dicom_client: DicomClient,
    orthanc_node: DicomNode,
    small_mr_study: StudyResult,
    mr_series: SeriesResult,
    storage_scp: StorageSCP,
) -> None:
    """C-MOVE and C-GET return the same set of SOPInstanceUIDs."""
    # C-GET
    cget_result = await dicom_client.get_series_to_memory(
        study_uid=small_mr_study.study_instance_uid,
        series_uid=mr_series.series_instance_uid,
        peer=orthanc_node,
    )
    cget_uids = set(cget_result.instances.keys())

    # C-MOVE
    from clarinet.services.dicom.models import (
        AssociationConfig,
        QueryRetrieveLevel,
        RetrieveRequest,
        StorageConfig,
        StorageMode,
    )
    from clarinet.services.dicom.operations import DicomOperations

    config = AssociationConfig(
        calling_aet=CALLING_AET,
        called_aet=PACS_AET,
        peer_host=PACS_HOST,
        peer_port=PACS_PORT,
    )
    request = RetrieveRequest(
        level=QueryRetrieveLevel.SERIES,
        study_instance_uid=small_mr_study.study_instance_uid,
        series_instance_uid=mr_series.series_instance_uid,
    )
    storage = StorageConfig(mode=StorageMode.MEMORY)

    ops = DicomOperations(calling_aet=CALLING_AET)
    cmove_result = await asyncio.to_thread(
        ops.retrieve_via_move,
        config,
        request,
        storage,
        CALLING_AET,
        storage_scp,
        timeout=120.0,
    )
    cmove_uids = set(cmove_result.instances.keys())

    assert cget_uids == cmove_uids, (
        f"C-GET returned {len(cget_uids)} UIDs, C-MOVE returned {len(cmove_uids)} UIDs"
    )
