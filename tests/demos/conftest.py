"""Fixtures for interactive Slicer demos.

Demos require a running 3D Slicer (localhost:2016) and Orthanc PACS on klara.
They are NOT part of CI — run via ``make slicer-demo-*`` targets.
"""

import socket
from pathlib import Path
from typing import Any

import pytest
import requests

from clarinet.services.slicer.service import SlicerService
from tests.config import (
    PACS_AET,
    PACS_DICOM_PORT,
    PACS_HOST,
    PACS_REST_URL,
    SLICER_HOST,
    SLICER_PORT,
)

# ---------------------------------------------------------------------------
# Constants (same as tests/e2e/test_slicer_pacs_workflow.py)
# ---------------------------------------------------------------------------

PACS_PORT = PACS_DICOM_PORT
CALLING_AET = "SLICER_TEST"


# ---------------------------------------------------------------------------
# Skip guards
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _check_slicer() -> None:
    """Skip all demos if 3D Slicer is unreachable."""
    try:
        sock = socket.create_connection((SLICER_HOST, SLICER_PORT), timeout=3)
        sock.close()
    except OSError:
        pytest.skip(f"3D Slicer not reachable at {SLICER_HOST}:{SLICER_PORT}")


@pytest.fixture(scope="session")
def _check_pacs() -> None:
    """Skip all demos if Orthanc PACS is unreachable."""
    try:
        resp = requests.get(f"{PACS_REST_URL}/system", timeout=2)
        resp.raise_for_status()
    except (requests.ConnectionError, requests.Timeout, requests.HTTPError):
        pytest.skip(f"Orthanc PACS not reachable at {PACS_REST_URL}")


@pytest.fixture(autouse=True)
def _require_slicer_and_pacs(_check_slicer: None, _check_pacs: None) -> None:
    """Auto-use: skip if either Slicer or PACS is unavailable."""


# ---------------------------------------------------------------------------
# Slicer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def slicer_service(_check_slicer: None) -> SlicerService:
    return SlicerService()


@pytest.fixture
def slicer_url(_check_slicer: None) -> str:
    return f"http://{SLICER_HOST}:{SLICER_PORT}"


@pytest.fixture
def pacs_monkey_patch() -> str:
    """Script block that monkey-patches PacsHelper.from_slicer with explicit PACS params."""
    return f"""
PacsHelper.from_slicer = classmethod(lambda cls, server_name=None: PacsHelper(
    host='{PACS_HOST}', port={PACS_PORT},
    called_aet='{PACS_AET}', calling_aet='{CALLING_AET}',
    retrieve_mode='c-get', move_aet='{CALLING_AET}',
))
"""


@pytest.fixture
def demo_working_dir(tmp_path: Path) -> Path:
    d = tmp_path / "slicer_demo"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Orthanc discovery fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def orthanc_first_study(_check_pacs: None) -> dict[str, Any]:
    """Find the smallest study on Orthanc for simple demos."""
    resp = requests.post(
        f"{PACS_REST_URL}/tools/find",
        json={"Level": "Study", "Query": {}},
        timeout=5,
    )
    resp.raise_for_status()
    orthanc_ids = resp.json()
    assert orthanc_ids, "No studies found on test PACS"

    best: dict[str, Any] | None = None
    best_instances = float("inf")
    for oid in orthanc_ids[:10]:
        info = requests.get(f"{PACS_REST_URL}/studies/{oid}", timeout=5).json()
        stats = requests.get(f"{PACS_REST_URL}/studies/{oid}/statistics", timeout=5).json()
        count = int(stats.get("CountInstances", 0))
        if 0 < count < best_instances:
            best_instances = count
            best = {
                "orthanc_id": oid,
                "study_uid": info["MainDicomTags"]["StudyInstanceUID"],
                "instance_count": count,
            }
    assert best is not None, "No study with instances found on test PACS"
    return best


@pytest.fixture(scope="session")
def orthanc_patient_with_two_studies(
    _check_pacs: None,
) -> dict[str, Any]:
    """Find a patient with 2+ studies on Orthanc for dual-layout demos.

    Returns:
        Dict with ``patient_id`` and ``studies`` list, each containing
        ``study_uid`` and ``series`` (list of ``{series_uid}``).
    """
    resp = requests.get(f"{PACS_REST_URL}/patients", timeout=5)
    resp.raise_for_status()
    patient_ids = resp.json()

    for pid in patient_ids:
        patient = requests.get(f"{PACS_REST_URL}/patients/{pid}", timeout=5).json()
        study_ids = patient.get("Studies", [])
        if len(study_ids) < 2:
            continue

        studies: list[dict[str, Any]] = []
        for sid in study_ids[:4]:
            study = requests.get(f"{PACS_REST_URL}/studies/{sid}", timeout=5).json()
            study_uid = study["MainDicomTags"]["StudyInstanceUID"]

            series_resp = requests.get(f"{PACS_REST_URL}/studies/{sid}/series", timeout=5).json()
            series_list = [
                {"series_uid": s["MainDicomTags"]["SeriesInstanceUID"]}
                for s in series_resp
                if s.get("Instances")
            ]
            if series_list:
                studies.append({"study_uid": study_uid, "series": series_list})

        if len(studies) >= 2:
            patient_name = patient.get("MainDicomTags", {}).get("PatientName", pid)
            return {"patient_id": patient_name, "studies": studies}

    pytest.skip("No patient with 2+ studies found on Orthanc PACS")


@pytest.fixture(scope="session")
def orthanc_two_series_same_patient(
    _check_pacs: None,
) -> dict[str, Any]:
    """Find a patient with 2+ series (from any studies) for alignment demos.

    Picks two series from the same patient so they share anatomy and
    coordinate space, making alignment visually meaningful.

    Returns:
        Dict with ``patient_id``, ``series_a`` and ``series_b``, each
        containing ``study_uid`` and ``series_uid``.
    """
    resp = requests.get(f"{PACS_REST_URL}/patients", timeout=5)
    resp.raise_for_status()

    for pid in resp.json():
        patient = requests.get(f"{PACS_REST_URL}/patients/{pid}", timeout=5).json()
        patient_name = patient.get("MainDicomTags", {}).get("PatientName", pid)

        all_series: list[dict[str, str]] = []
        for sid in patient.get("Studies", []):
            study = requests.get(f"{PACS_REST_URL}/studies/{sid}", timeout=5).json()
            study_uid = study["MainDicomTags"]["StudyInstanceUID"]

            for s in requests.get(f"{PACS_REST_URL}/studies/{sid}/series", timeout=5).json():
                if not s.get("Instances"):
                    continue
                all_series.append(
                    {
                        "study_uid": study_uid,
                        "series_uid": s["MainDicomTags"]["SeriesInstanceUID"],
                    }
                )

        if len(all_series) >= 2:
            return {
                "patient_id": patient_name,
                "series_a": all_series[0],
                "series_b": all_series[1],
            }

    pytest.skip("No patient with 2+ series found on Orthanc PACS")
