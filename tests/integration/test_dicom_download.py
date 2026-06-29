"""Integration tests for DICOM series ZIP download endpoint.

Tests the GET /dicom-web/studies/{study_uid}/series/{series_uid}/archive
endpoint with real pydicom datasets (no PACS mocks for cache content).
"""

import io
import zipfile
from collections.abc import AsyncGenerator
from pathlib import Path
from unittest.mock import MagicMock

import pydicom
import pytest
import pytest_asyncio
from dimsechord import DicomCache
from httpx import ASGITransport, AsyncClient
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

from clarinet.api.app import app
from clarinet.api.dependencies import get_dicomweb_filler
from clarinet.services.dicomweb.filler import CacheFiller
from tests.conftest import create_authenticated_client, create_mock_superuser

pytestmark = pytest.mark.asyncio

STUDY_UID = "1.2.826.0.1.3680043.8.498.1111111"
SERIES_UID = "1.2.826.0.1.3680043.8.498.2222222"
SOP_UIDS = [
    "1.2.826.0.1.3680043.8.498.3333331",
    "1.2.826.0.1.3680043.8.498.3333332",
    "1.2.826.0.1.3680043.8.498.3333333",
]


def _make_dicom_dataset(sop_uid: str) -> Dataset:
    """Create a minimal but valid DICOM dataset."""
    ds = Dataset()
    ds.SOPInstanceUID = sop_uid
    ds.StudyInstanceUID = STUDY_UID
    ds.SeriesInstanceUID = SERIES_UID
    ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"  # CT Image Storage
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


@pytest.fixture
def dicomweb_filler(tmp_path: Path) -> CacheFiller:
    """A CacheFiller over a dimsechord DicomCache, pre-populated in memory.

    ``ensure_series`` returns the in-memory entry (memory hit) without touching
    the engine; ``session_factory=None`` disables the dcm_anon tier.
    """
    cache_dir = tmp_path / "dicomweb_cache"
    cache_dir.mkdir()
    cache = DicomCache(
        base_dir=cache_dir,
        index_path=cache_dir / "index.db",
        memory_ttl_minutes=30,
        memory_max_entries=50,
    )
    instances = {sop_uid: _make_dicom_dataset(sop_uid) for sop_uid in SOP_UIDS}
    cache.put_series_to_memory(STUDY_UID, SERIES_UID, instances, disk_persisted=True)
    return CacheFiller(
        cache=cache,
        engine=MagicMock(),
        client=MagicMock(),
        pacs=MagicMock(),
        retrieve_mode="c-get",
        session_factory=None,
        storage_path=tmp_path,
    )


@pytest_asyncio.fixture
async def client(
    test_session,
    test_settings,
    dicomweb_filler,
) -> AsyncGenerator[AsyncClient]:
    """Authenticated client with DICOMweb filler override."""
    mock_user = await create_mock_superuser(test_session, email="dicom_dl@test.com")

    app.dependency_overrides[get_dicomweb_filler] = lambda: dicomweb_filler

    async for ac in create_authenticated_client(mock_user, test_session, test_settings):
        yield ac

    app.dependency_overrides.pop(get_dicomweb_filler, None)


@pytest_asyncio.fixture
async def no_auth_client(test_session, test_settings) -> AsyncGenerator[AsyncClient]:
    """Client without auth overrides for 401 tests."""
    from clarinet.utils.database import get_async_session

    async def override_get_session():
        yield test_session

    async def override_get_settings():
        return test_settings

    app.dependency_overrides[get_async_session] = override_get_session

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
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_download_series_archive_happy_path(client: AsyncClient) -> None:
    """GET archive returns 200 with correct content-type and disposition."""
    resp = await client.get(f"/dicom-web/studies/{STUDY_UID}/series/{SERIES_UID}/archive")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert SERIES_UID in resp.headers["content-disposition"]


async def test_download_series_archive_zip_content(client: AsyncClient) -> None:
    """Downloaded ZIP contains valid DICOM files with correct UIDs."""
    resp = await client.get(f"/dicom-web/studies/{STUDY_UID}/series/{SERIES_UID}/archive")
    assert resp.status_code == 200

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert len(names) == len(SOP_UIDS)

    extracted_sop_uids = set()
    for name in names:
        assert name.endswith(".dcm")
        data = zf.read(name)
        ds = pydicom.dcmread(io.BytesIO(data))
        assert str(ds.SeriesInstanceUID) == SERIES_UID
        assert str(ds.StudyInstanceUID) == STUDY_UID
        extracted_sop_uids.add(str(ds.SOPInstanceUID))

    assert extracted_sop_uids == set(SOP_UIDS)


async def test_download_series_archive_no_auth(no_auth_client: AsyncClient) -> None:
    """Request without auth cookie returns 401."""
    resp = await no_auth_client.get(f"/dicom-web/studies/{STUDY_UID}/series/{SERIES_UID}/archive")
    assert resp.status_code == 401
