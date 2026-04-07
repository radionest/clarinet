"""Integration tests for SlicerHelper.download_series_zip().

Tests the full cycle: real HTTP server → ZIP download → extract → validate DICOM.
Does NOT require a running 3D Slicer instance.
"""

import asyncio
import os
import threading
import time
from collections.abc import Generator
from pathlib import Path
from urllib.error import HTTPError

import pydicom
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

from clarinet.services.dicomweb.cache import DicomWebCache
from clarinet.services.slicer.helper import SlicerHelper

STUDY_UID = "1.2.826.0.1.3680043.8.498.5551111"
SERIES_UID = "1.2.826.0.1.3680043.8.498.5552222"
SOP_UIDS = [
    "1.2.826.0.1.3680043.8.498.5553331",
    "1.2.826.0.1.3680043.8.498.5553332",
]

_VALID_TOKEN = "test_valid_session_token"


def _make_dicom_dataset(sop_uid: str) -> Dataset:
    ds = Dataset()
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = STUDY_UID
    ds.SeriesInstanceUID = SERIES_UID
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    ds.Modality = "CT"
    ds.Rows = 2
    ds.Columns = 2
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PixelData = b"\x00" * 8

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
    file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = file_meta
    ds.preamble = b"\x00" * 128
    return ds


def _build_test_app(cache: DicomWebCache) -> FastAPI:
    """Build a minimal FastAPI app with just the archive endpoint (no lifespan)."""
    import tempfile

    from fastapi import Request
    from starlette.responses import JSONResponse

    test_app = FastAPI()

    @test_app.get("/dicom-web/studies/{study_uid}/series/{series_uid}/archive")
    async def download_archive(
        study_uid: str,
        series_uid: str,
        request: Request,
    ) -> StreamingResponse:
        cookie = request.cookies.get("clarinet_session", "")
        if cookie != _VALID_TOKEN:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})  # type: ignore[return-value]

        cached = cache._get_from_memory(study_uid, series_uid)
        if cached is None:
            return JSONResponse(status_code=404, content={"detail": "Not found"})  # type: ignore[return-value]

        spooled = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)  # noqa: SIM115
        await asyncio.to_thread(cache.build_series_zip, cached, spooled)
        spooled.seek(0)

        return StreamingResponse(
            spooled,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{series_uid}.zip"'},
        )

    return test_app


@pytest.fixture
def dicomweb_cache(tmp_path: Path) -> DicomWebCache:
    cache_dir = tmp_path / "dicomweb_cache"
    cache_dir.mkdir()
    cache = DicomWebCache(base_dir=cache_dir, memory_ttl_minutes=30, memory_max_entries=50)
    instances = {uid: _make_dicom_dataset(uid) for uid in SOP_UIDS}
    cache._put_to_memory(STUDY_UID, SERIES_UID, instances, disk_persisted=True)
    return cache


class _TestServer:
    """Minimal uvicorn server on a random port."""

    def __init__(self, app: FastAPI) -> None:
        self.port: int = 0
        self._app = app
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]

        config = uvicorn.Config(self._app, host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        for _ in range(50):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("Test server failed to start")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)


@pytest.fixture
def live_server(dicomweb_cache: DicomWebCache) -> Generator[str]:
    """Start a minimal HTTP server and yield its base URL."""
    test_app = _build_test_app(dicomweb_cache)
    server = _TestServer(test_app)
    server.start()
    yield f"http://127.0.0.1:{server.port}"
    server.stop()


@pytest.fixture
def slicer_helper(tmp_path: Path) -> SlicerHelper:
    """SlicerHelper with only working_folder set (no Slicer env needed)."""
    working = tmp_path / "slicer_work"
    working.mkdir()
    # Bypass __init__ which requires Slicer runtime (scene.Clear, layoutManager, etc.)
    helper = object.__new__(SlicerHelper)
    helper.working_folder = str(working)
    return helper


def test_download_series_zip(live_server: str, slicer_helper: SlicerHelper) -> None:
    """download_series_zip extracts valid DICOM files."""
    extract_dir = slicer_helper.download_series_zip(
        study_uid=STUDY_UID,
        series_uid=SERIES_UID,
        server_url=live_server,
        auth_cookie=f"clarinet_session={_VALID_TOKEN}",
    )

    assert os.path.isdir(extract_dir)

    dcm_files = [f for f in os.listdir(extract_dir) if f.endswith(".dcm")]
    assert len(dcm_files) == len(SOP_UIDS)

    extracted_sop_uids = set()
    for fname in dcm_files:
        ds = pydicom.dcmread(os.path.join(extract_dir, fname))
        assert str(ds.SeriesInstanceUID) == SERIES_UID
        extracted_sop_uids.add(str(ds.SOPInstanceUID))

    assert extracted_sop_uids == set(SOP_UIDS)


def test_download_series_zip_no_auth(live_server: str, slicer_helper: SlicerHelper) -> None:
    """download_series_zip with invalid auth raises HTTPError."""
    with pytest.raises(HTTPError) as exc_info:
        slicer_helper.download_series_zip(
            study_uid=STUDY_UID,
            series_uid=SERIES_UID,
            server_url=live_server,
            auth_cookie="clarinet_session=invalid",
        )
    assert exc_info.value.code == 401
