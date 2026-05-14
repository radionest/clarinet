"""Unit tests for the built-in DICOMweb cache prefetch pipeline task."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clarinet.exceptions.domain import PipelineStepError
from clarinet.models.base import DicomQueryLevel
from clarinet.services.dicom.models import RetrieveResult, SeriesResult
from clarinet.services.pipeline.context import FileResolver, RecordQuery, TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.tasks.cache_dicomweb import (
    _has_dcm_anon,
    _has_disk_cache,
    _organize_to_cache,
    _prefetch_dicom_web_impl,
)
from tests.utils.session import PassThroughSession


def _series_result(series_uid: str, study_uid: str = "STUDY1") -> MagicMock:
    """Build a SeriesResult-shaped mock with explicit attributes."""
    mock = MagicMock(spec=SeriesResult)
    mock.study_instance_uid = study_uid
    mock.series_instance_uid = series_uid
    return mock


def _retrieve_result(num_completed: int) -> MagicMock:
    """Build a RetrieveResult-shaped mock with explicit attributes."""
    mock = MagicMock(spec=RetrieveResult)
    mock.num_completed = num_completed
    mock.num_failed = 0
    mock.status = "Success"
    return mock


def _build_ctx(tmp_path: Path) -> TaskContext:
    """Build a minimal TaskContext for the prefetch task.

    The task does not use ``ctx.files`` for output (it writes directly
    to ``settings.storage_path/dicomweb_cache/...``), so the resolver is
    a stub keyed only by PATIENT level.
    """
    working_dirs = {DicomQueryLevel.PATIENT: tmp_path}
    files = FileResolver(
        working_dirs=working_dirs,
        record_type_level=DicomQueryLevel.PATIENT,
        file_registry=[],
        fields={},
    )
    client = AsyncMock()
    records = RecordQuery(client=client, files=files)
    msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3")
    return TaskContext(files=files, records=records, client=client, msg=msg)


def _make_dcm(path: Path, series_uid: str, sop_uid: str) -> None:
    """Write a minimal DICOM file readable by ``pydicom.dcmread``."""
    import pydicom
    from pydicom.dataset import FileMetaDataset

    file_meta = FileMetaDataset()
    file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
    file_meta.MediaStorageSOPInstanceUID = sop_uid
    file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"

    ds = pydicom.Dataset()
    ds.file_meta = file_meta
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.PatientName = "Test"
    ds.PatientID = "PAT001"
    ds.StudyInstanceUID = "1.2.3"
    ds.is_little_endian = True
    ds.is_implicit_VR = True
    path.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(path, enforce_file_format=True)


class TestHasDiskCache:
    """Tests for the disk cache presence check.

    No TTL check: DICOM on the PACS is immutable, so any present entry
    is valid until the cleanup service physically removes it.
    """

    def test_missing_marker_returns_false(self, tmp_path: Path):
        assert _has_disk_cache(tmp_path, "1.2.3", "1.2.3.4") is False

    def test_missing_dcm_files_returns_false(self, tmp_path: Path):
        series_dir = tmp_path / "1.2.3" / "1.2.3.4"
        series_dir.mkdir(parents=True)
        (series_dir / ".cached_at").write_text(str(time.time()))
        assert _has_disk_cache(tmp_path, "1.2.3", "1.2.3.4") is False

    def test_old_marker_still_valid(self, tmp_path: Path):
        """An ancient marker is still a cache hit — lifecycle is cleanup's job."""
        series_dir = tmp_path / "1.2.3" / "1.2.3.4"
        series_dir.mkdir(parents=True)
        (series_dir / ".cached_at").write_text(str(time.time() - 7 * 86400))
        (series_dir / "instance.dcm").write_bytes(b"fake")
        assert _has_disk_cache(tmp_path, "1.2.3", "1.2.3.4") is True

    def test_valid_cache_returns_true(self, tmp_path: Path):
        series_dir = tmp_path / "1.2.3" / "1.2.3.4"
        series_dir.mkdir(parents=True)
        (series_dir / ".cached_at").write_text(str(time.time()))
        (series_dir / "instance.dcm").write_bytes(b"fake")
        assert _has_disk_cache(tmp_path, "1.2.3", "1.2.3.4") is True


async def _seed_anonymized(
    test_session, *, patient_id: str, auto_id: int, study_uid: str, series_uid: str
):
    """Seed Patient/Study/Series with anon_uid set."""
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study
    from tests.utils.factories import make_patient

    patient = make_patient(patient_id, "Test", auto_id=auto_id)
    test_session.add(patient)
    await test_session.commit()
    study = Study(
        patient_id=patient_id,
        study_uid=study_uid,
        date=datetime.now(UTC).date(),
        modalities_in_study="CT",
        anon_uid="ANON_STUDY",
    )
    test_session.add(study)
    await test_session.commit()
    series = Series(
        study_uid=study_uid,
        series_uid=series_uid,
        series_number=1,
        modality="CT",
        anon_uid="ANON_SERIES",
    )
    test_session.add(series)
    await test_session.commit()
    return patient, study, series


class TestHasDcmAnon:
    """Tests for the dcm_anon presence check (DB-aware via disk_path_template)."""

    @pytest.mark.asyncio
    async def test_no_series_in_db_returns_false(self, tmp_path: Path, test_session) -> None:
        """No Series row → can't resolve path → returns False without scanning disk."""
        with patch("clarinet.services.pipeline.tasks.cache_dicomweb.db_manager") as mock_dbm:
            mock_dbm.async_session_factory = lambda: PassThroughSession(test_session)
            assert await _has_dcm_anon(tmp_path, "no.such.study", "no.such.series") is False

    @pytest.mark.asyncio
    async def test_no_anon_dir_returns_false(self, tmp_path: Path, test_session) -> None:
        """Series in DB but dcm_anon dir missing on disk → False."""
        await _seed_anonymized(
            test_session,
            patient_id="HAS_NO_ANON_DIR",
            auto_id=501,
            study_uid="1.2.8001.1",
            series_uid="1.2.8001.1.1",
        )
        with patch("clarinet.services.pipeline.tasks.cache_dicomweb.db_manager") as mock_dbm:
            mock_dbm.async_session_factory = lambda: PassThroughSession(test_session)
            assert await _has_dcm_anon(tmp_path, "1.2.8001.1", "1.2.8001.1.1") is False

    @pytest.mark.asyncio
    async def test_empty_anon_dir_returns_false(self, tmp_path: Path, test_session) -> None:
        """Series in DB, dcm_anon dir exists but empty → False (no .dcm files)."""
        from clarinet.models.base import DicomQueryLevel
        from clarinet.services.common.storage_paths import build_context, render_working_folder

        patient, study, series = await _seed_anonymized(
            test_session,
            patient_id="HAS_EMPTY_ANON",
            auto_id=502,
            study_uid="1.2.8002.1",
            series_uid="1.2.8002.1.1",
        )
        ctx = build_context(patient=patient, study=study, series=series)
        series_dir = render_working_folder(
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            DicomQueryLevel.SERIES,
            ctx,
            tmp_path,
        )
        (series_dir / "dcm_anon").mkdir(parents=True)

        with patch("clarinet.services.pipeline.tasks.cache_dicomweb.db_manager") as mock_dbm:
            mock_dbm.async_session_factory = lambda: PassThroughSession(test_session)
            assert await _has_dcm_anon(tmp_path, "1.2.8002.1", "1.2.8002.1.1") is False

    @pytest.mark.asyncio
    async def test_finds_dcm_via_resolved_path(self, tmp_path: Path, test_session) -> None:
        """Series in DB + dcm_anon dir with .dcm files at template-resolved path → True."""
        from clarinet.models.base import DicomQueryLevel
        from clarinet.services.common.storage_paths import build_context, render_working_folder

        patient, study, series = await _seed_anonymized(
            test_session,
            patient_id="HAS_DCM_ANON",
            auto_id=503,
            study_uid="1.2.8003.1",
            series_uid="1.2.8003.1.1",
        )
        ctx = build_context(patient=patient, study=study, series=series)
        series_dir = render_working_folder(
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            DicomQueryLevel.SERIES,
            ctx,
            tmp_path,
        )
        dcm_anon = series_dir / "dcm_anon"
        dcm_anon.mkdir(parents=True)
        (dcm_anon / "inst.dcm").write_bytes(b"placeholder")

        with patch("clarinet.services.pipeline.tasks.cache_dicomweb.db_manager") as mock_dbm:
            mock_dbm.async_session_factory = lambda: PassThroughSession(test_session)
            assert await _has_dcm_anon(tmp_path, "1.2.8003.1", "1.2.8003.1.1") is True


class TestOrganizeToCache:
    """Tests for moving retrieved DICOMs into the cache layout."""

    def test_groups_by_series_instance_uid(self, tmp_path: Path):
        tmp_dir = tmp_path / "tmp"
        cache_base = tmp_path / "cache"
        tmp_dir.mkdir()

        _make_dcm(tmp_dir / "a.dcm", "SER1", "SOP1")
        _make_dcm(tmp_dir / "b.dcm", "SER1", "SOP2")
        _make_dcm(tmp_dir / "c.dcm", "SER2", "SOP3")

        grouped = _organize_to_cache(tmp_dir, cache_base, study_uid="STUDY1")

        assert grouped == {"SER1": 2, "SER2": 1}
        assert (cache_base / "STUDY1" / "SER1" / "SOP1.dcm").exists()
        assert (cache_base / "STUDY1" / "SER1" / "SOP2.dcm").exists()
        assert (cache_base / "STUDY1" / "SER2" / "SOP3.dcm").exists()
        assert (cache_base / "STUDY1" / "SER1" / ".cached_at").exists()
        assert (cache_base / "STUDY1" / "SER2" / ".cached_at").exists()

    def test_skips_unreadable_files(self, tmp_path: Path):
        tmp_dir = tmp_path / "tmp"
        cache_base = tmp_path / "cache"
        tmp_dir.mkdir()

        _make_dcm(tmp_dir / "good.dcm", "SER1", "SOP1")
        (tmp_dir / "bad.dcm").write_bytes(b"not a real DICOM file")

        grouped = _organize_to_cache(tmp_dir, cache_base, study_uid="STUDY1")

        assert grouped == {"SER1": 1}
        assert (cache_base / "STUDY1" / "SER1" / "SOP1.dcm").exists()

    def test_skips_dicom_with_missing_uids(self, tmp_path: Path):
        """A readable DICOM lacking SeriesInstanceUID/SOPInstanceUID is skipped."""
        import pydicom
        from pydicom.dataset import FileMetaDataset

        tmp_dir = tmp_path / "tmp"
        cache_base = tmp_path / "cache"
        tmp_dir.mkdir()

        # Build a DICOM that omits both UIDs
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        file_meta.MediaStorageSOPInstanceUID = "1.2.3"
        file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"
        ds = pydicom.Dataset()
        ds.file_meta = file_meta
        ds.PatientName = "Anon"
        ds.is_little_endian = True
        ds.is_implicit_VR = True
        bad = tmp_dir / "no_uids.dcm"
        ds.save_as(bad, enforce_file_format=True)

        _make_dcm(tmp_dir / "good.dcm", "SER1", "SOP1")

        grouped = _organize_to_cache(tmp_dir, cache_base, study_uid="STUDY1")

        assert grouped == {"SER1": 1}
        assert (cache_base / "STUDY1" / "SER1" / "SOP1.dcm").exists()

    def test_refetch_clears_stale_marker_first(self, tmp_path: Path):
        """Re-fetch must remove .cached_at *before* moving fresh files.

        Guarantees atomic publication from the OHIF reader's perspective:
        the API process must never see a present marker pointing at a
        directory that holds a mix of stale and fresh *.dcm files.
        """
        tmp_dir = tmp_path / "tmp"
        cache_base = tmp_path / "cache"
        tmp_dir.mkdir()

        # Pre-populate stale entry: old SOP files + expired .cached_at
        old_series_dir = cache_base / "STUDY1" / "SER1"
        old_series_dir.mkdir(parents=True)
        (old_series_dir / "STALE_SOP_001.dcm").write_bytes(b"stale1")
        (old_series_dir / "STALE_SOP_002.dcm").write_bytes(b"stale2")
        (old_series_dir / ".cached_at").write_text("0.0")  # 1970, definitely expired

        # New fetch arrives
        _make_dcm(tmp_dir / "fresh.dcm", "SER1", "FRESH_SOP")
        grouped = _organize_to_cache(tmp_dir, cache_base, study_uid="STUDY1")

        assert grouped == {"SER1": 1}
        # Stale files removed
        assert not (old_series_dir / "STALE_SOP_001.dcm").exists()
        assert not (old_series_dir / "STALE_SOP_002.dcm").exists()
        # Fresh file present
        assert (old_series_dir / "FRESH_SOP.dcm").exists()
        # Marker rewritten with current timestamp (not the stale 0.0)
        new_cached_at = float((old_series_dir / ".cached_at").read_text())
        assert new_cached_at > time.time() - 60


class TestPrefetchDicomWebImpl:
    """Tests for the core prefetch logic."""

    @pytest.fixture(autouse=True)
    def _stub_has_dcm_anon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default to ``_has_dcm_anon`` → False.

        ``_has_dcm_anon`` queries the DB via ``db_manager.async_session_factory``
        to resolve the template-rendered path; without setting up a real DB
        and Series row for every test, the query would fail. The dcm-anon-
        skip flow has dedicated tests that override this stub.
        """

        async def _no_anon(*args, **kwargs):
            return False

        monkeypatch.setattr(
            "clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon",
            _no_anon,
        )

    @pytest.mark.asyncio
    async def test_missing_study_uid_raises(self, tmp_path: Path):
        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="")
        with pytest.raises(PipelineStepError, match="study_uid is required"):
            await _prefetch_dicom_web_impl(msg, ctx)

    @pytest.mark.asyncio
    async def test_non_bool_skip_if_anon_raises(self, tmp_path: Path):
        """Reject non-bool payload values to prevent silent intent inversion."""
        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(
            patient_id="PAT001", study_uid="STUDY1", payload={"skip_if_anon": "false"}
        )
        with pytest.raises(PipelineStepError, match="skip_if_anon must be a bool"):
            await _prefetch_dicom_web_impl(msg, ctx)

    @pytest.mark.asyncio
    async def test_no_series_returns_gracefully(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=[])

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        mock_client.find_series.assert_awaited_once()
        # No C-GET attempted
        mock_client.get_study.assert_not_called()
        mock_client.get_series.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_all_series_cached(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        # Pre-populate disk cache for both series
        cache_base = tmp_path / "dicomweb_cache"
        for series_uid in ("SER1", "SER2"):
            series_dir = cache_base / "STUDY1" / series_uid
            series_dir.mkdir(parents=True)
            (series_dir / "inst.dcm").write_bytes(b"fake")
            (series_dir / ".cached_at").write_text(str(time.time()))

        series_results = [_series_result("SER1"), _series_result("SER2")]
        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=series_results)
        mock_client.get_study = AsyncMock()
        mock_client.get_series = AsyncMock()

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        mock_client.get_study.assert_not_called()
        mock_client.get_series.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_dcm_anon_by_default(self, tmp_path: Path, monkeypatch):
        """When ``_has_dcm_anon`` says True, no C-GET is attempted."""
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        async def _yes_anon(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon",
            _yes_anon,
        )

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=[_series_result("SER1")])
        mock_client.get_study = AsyncMock()
        mock_client.get_series = AsyncMock()

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        mock_client.get_study.assert_not_called()
        mock_client.get_series.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_if_anon_false_forces_fetch(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(
            patient_id="PAT001", study_uid="STUDY1", payload={"skip_if_anon": False}
        )

        # dcm_anon present but should be ignored
        anon = tmp_path / "PAT001" / "STUDY1" / "SER1" / "dcm_anon"
        anon.mkdir(parents=True)
        (anon / "inst.dcm").write_bytes(b"fake")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=[_series_result("SER1")])

        async def fake_get_study(study_uid, peer, output_dir):
            _make_dcm(output_dir / "fetched.dcm", "SER1", "SOP-NEW")
            return _retrieve_result(num_completed=1)

        mock_client.get_study = AsyncMock(side_effect=fake_get_study)

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        mock_client.get_study.assert_awaited_once()
        # File landed in the cache layout
        cache_dir = tmp_path / "dicomweb_cache" / "STUDY1" / "SER1"
        assert (cache_dir / "SOP-NEW.dcm").exists()
        assert (cache_dir / ".cached_at").exists()

    @pytest.mark.asyncio
    async def test_full_study_uses_single_get(self, tmp_path: Path, monkeypatch):
        """When all series are missing, one study-level C-GET is used."""
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(
            return_value=[_series_result("SER1"), _series_result("SER2")]
        )

        async def fake_get_study(study_uid, peer, output_dir):
            _make_dcm(output_dir / "a.dcm", "SER1", "SOP1")
            _make_dcm(output_dir / "b.dcm", "SER2", "SOP2")
            return _retrieve_result(num_completed=2)

        mock_client.get_study = AsyncMock(side_effect=fake_get_study)
        mock_client.get_series = AsyncMock()

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        mock_client.get_study.assert_awaited_once()
        mock_client.get_series.assert_not_called()
        assert (tmp_path / "dicomweb_cache" / "STUDY1" / "SER1" / "SOP1.dcm").exists()
        assert (tmp_path / "dicomweb_cache" / "STUDY1" / "SER2" / "SOP2.dcm").exists()

    @pytest.mark.asyncio
    async def test_partial_cache_uses_per_series_get(self, tmp_path: Path, monkeypatch):
        """When only some series are missing, only those are retrieved per-series."""
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        # SER1 already cached
        cache_base = tmp_path / "dicomweb_cache"
        (cache_base / "STUDY1" / "SER1").mkdir(parents=True)
        (cache_base / "STUDY1" / "SER1" / "old.dcm").write_bytes(b"fake")
        (cache_base / "STUDY1" / "SER1" / ".cached_at").write_text(str(time.time()))

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(
            return_value=[_series_result("SER1"), _series_result("SER2")]
        )

        async def fake_get_series(study_uid, series_uid, peer, output_dir):
            _make_dcm(output_dir / "new.dcm", series_uid, "SOP-NEW")
            return _retrieve_result(num_completed=1)

        mock_client.get_study = AsyncMock()
        mock_client.get_series = AsyncMock(side_effect=fake_get_series)

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        mock_client.get_study.assert_not_called()
        mock_client.get_series.assert_awaited_once()
        # SER2 was fetched
        assert (cache_base / "STUDY1" / "SER2" / "SOP-NEW.dcm").exists()

    @pytest.mark.asyncio
    async def test_zero_instances_raises_when_full_study(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=[_series_result("SER1")])
        mock_client.get_study = AsyncMock(return_value=_retrieve_result(num_completed=0))

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
            pytest.raises(PipelineStepError, match="0 instances"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)
