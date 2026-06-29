"""Unit tests for the built-in DICOMweb cache prefetch pipeline task."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clarinet.client import ClarinetAPIError
from clarinet.exceptions.domain import PipelineStepError
from clarinet.files import Files
from clarinet.models.base import DicomQueryLevel
from clarinet.services.dicom.models import SeriesResult
from clarinet.services.pipeline.context import RecordQuery, TaskContext
from clarinet.services.pipeline.message import PipelineMessage
from clarinet.services.pipeline.tasks.cache_dicomweb import (
    _filter_series_to_fetch,
    _has_dcm_anon,
    _prefetch_dicom_web_impl,
)
from clarinet.settings import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dimsechord import DicomCache


def _series_result(series_uid: str, study_uid: str = "STUDY1") -> MagicMock:
    """Build a SeriesResult-shaped mock with explicit attributes."""
    mock = MagicMock(spec=SeriesResult)
    mock.study_instance_uid = study_uid
    mock.series_instance_uid = series_uid
    return mock


def _build_ctx(tmp_path: Path) -> TaskContext:
    """Build a minimal TaskContext for the prefetch task.

    The task does not use ``ctx.files`` for output (it writes directly to the
    dimsechord cache under ``settings.storage_path/dicomweb_cache/...``), so
    the resolver is a stub keyed only by PATIENT level.
    """
    files = Files.empty()
    files._dirs = {DicomQueryLevel.PATIENT: tmp_path}
    files._level = DicomQueryLevel.PATIENT
    client = AsyncMock()
    records = RecordQuery(client=client, files=files)
    msg = PipelineMessage(patient_id="PAT001", study_uid="1.2.3")
    return TaskContext(files=files, records=records, client=client, msg=msg)


def _make_dataset(series_uid: str, sop_uid: str, study_uid: str = "STUDY1"):
    """Build a minimal pydicom Dataset writable by ``DicomCache.write_instance``."""
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
    ds.StudyInstanceUID = study_uid
    ds.is_little_endian = True
    ds.is_implicit_VR = True
    return ds


def _open_cache(tmp_path: Path) -> DicomCache:
    """Open a DicomCache at the same layout the prefetch task builds."""
    from dimsechord import DicomCache

    cache_dir = tmp_path / "dicomweb_cache"
    return DicomCache(base_dir=cache_dir, index_path=cache_dir / "index.db")


def _seed_index(tmp_path: Path, study_uid: str, series_uid: str, sop_uids: list[str]) -> None:
    """Write instances into the shared SQLite index so ``series_cached`` is True."""
    cache = _open_cache(tmp_path)
    try:
        for sop_uid in sop_uids:
            cache.write_instance(
                study_uid, series_uid, sop_uid, _make_dataset(series_uid, sop_uid, study_uid)
            )
    finally:
        cache.shutdown()


def _series_cached(tmp_path: Path, study_uid: str, series_uid: str) -> bool:
    """Check the on-disk SQLite index for a warmed series (fresh connection)."""
    cache = _open_cache(tmp_path)
    try:
        return cache.series_cached(study_uid, series_uid)
    finally:
        cache.shutdown()


class _FakeEngine:
    """Stand-in for dimsechord ``PullEngine``.

    Records which series were asked for and tees the planned instances into
    the *real* cache the task built (matching the engine's disk+index tee), so
    tests assert on ``cache.series_cached`` exactly like production.

    ``ensure_series`` is the partial-miss path (one association per series);
    ``stream_study`` is the cold-study path — ONE study-level association that
    streams + tees every series, mirroring ``PullEngine.stream_study``.
    """

    def __init__(self, cache: DicomCache, plan: dict[str, list[str]]) -> None:
        self._cache = cache
        self._plan = plan  # series_uid -> list of sop_uids "retrieved" from PACS
        self.calls: list[str] = []
        self.study_calls: list[list[str]] = []

    def _tee(self, study_uid: str, series_uid: str) -> dict:
        instances = {}
        for sop_uid in self._plan.get(series_uid, []):
            ds = _make_dataset(series_uid, sop_uid, study_uid)
            self._cache.write_instance(study_uid, series_uid, sop_uid, ds)
            instances[sop_uid] = ds
        return instances

    async def ensure_series(self, study_uid: str, series_uid: str):
        self.calls.append(series_uid)
        instances = self._tee(study_uid, series_uid)
        return self._cache.put_series_to_memory(
            study_uid, series_uid, instances, disk_persisted=True
        )

    async def stream_study(self, study_uid: str, series_uids: list[str]):
        # ONE study-level association covering all requested series.
        self.study_calls.append(list(series_uids))
        for series_uid in series_uids:
            for ds in self._tee(study_uid, series_uid).values():
                yield ds


def _patch_engine(monkeypatch: pytest.MonkeyPatch, plan: dict[str, list[str]]) -> dict[str, object]:
    """Patch ``_build_engine`` to return a ``_FakeEngine`` wired to the task's cache."""
    holder: dict[str, object] = {}

    def _build(cache, pacs):
        engine = _FakeEngine(cache, plan)
        holder["engine"] = engine
        return engine

    monkeypatch.setattr("clarinet.services.pipeline.tasks.cache_dicomweb._build_engine", _build)
    return holder


def _make_anonymized_triple(
    *,
    patient_id: str,
    auto_id: int,
    study_uid: str,
    series_uid: str,
):
    """Build Patient/Study/Series with anon_uid set — in memory only.

    Matches the post-API contract of ``_has_dcm_anon``: the function takes
    already-loaded entities (originally from ``ctx.client.get_study()``), so
    tests build objects directly without touching the DB.
    """
    from datetime import UTC, datetime

    from clarinet.models.study import Series, Study
    from tests.utils.factories import make_patient

    patient = make_patient(patient_id, "Test", auto_id=auto_id)
    study = Study(
        patient_id=patient_id,
        study_uid=study_uid,
        date=datetime.now(UTC).date(),
        modalities_in_study="CT",
        anon_uid="ANON_STUDY",
    )
    series = Series(
        study_uid=study_uid,
        series_uid=series_uid,
        series_number=1,
        modality="CT",
        anon_uid="ANON_SERIES",
    )
    return patient, study, series


class TestHasDcmAnon:
    """Tests for the dcm_anon presence check.

    ``_has_dcm_anon`` does not touch the DB — it takes pre-loaded
    Patient/Study/Series and renders the storage template against them. The
    function is synchronous (production wraps it in ``asyncio.to_thread`` from
    ``_filter_series_to_fetch``).
    """

    def test_no_anon_dir_returns_false(self, tmp_path: Path) -> None:
        """Entities resolve a path, but dcm_anon dir missing on disk → False."""
        patient, study, series = _make_anonymized_triple(
            patient_id="HAS_NO_ANON_DIR",
            auto_id=501,
            study_uid="1.2.8001.1",
            series_uid="1.2.8001.1.1",
        )
        assert _has_dcm_anon(tmp_path, patient, study, series) is False

    def test_empty_anon_dir_returns_false(self, tmp_path: Path) -> None:
        """dcm_anon dir exists but contains no ``.dcm`` files → False."""
        from clarinet.files._storage import build_context, render_working_folder

        patient, study, series = _make_anonymized_triple(
            patient_id="HAS_EMPTY_ANON",
            auto_id=502,
            study_uid="1.2.8002.1",
            series_uid="1.2.8002.1.1",
        )
        ctx = build_context(patient=patient, study=study, series=series)
        series_dir = render_working_folder(
            settings.disk_path_template,
            DicomQueryLevel.SERIES,
            ctx,
            tmp_path,
        )
        (series_dir / "dcm_anon").mkdir(parents=True)

        assert _has_dcm_anon(tmp_path, patient, study, series) is False

    def test_finds_dcm_via_resolved_path(self, tmp_path: Path) -> None:
        """dcm_anon dir with ``.dcm`` files at template-resolved path → True."""
        from clarinet.files._storage import build_context, render_working_folder

        patient, study, series = _make_anonymized_triple(
            patient_id="HAS_DCM_ANON",
            auto_id=503,
            study_uid="1.2.8003.1",
            series_uid="1.2.8003.1.1",
        )
        ctx = build_context(patient=patient, study=study, series=series)
        series_dir = render_working_folder(
            settings.disk_path_template,
            DicomQueryLevel.SERIES,
            ctx,
            tmp_path,
        )
        dcm_anon = series_dir / "dcm_anon"
        dcm_anon.mkdir(parents=True)
        (dcm_anon / "inst.dcm").write_bytes(b"placeholder")

        assert _has_dcm_anon(tmp_path, patient, study, series) is True

    def test_accepts_dto_shape_from_api(self, tmp_path: Path) -> None:
        """`_has_dcm_anon` works with DTOs (PatientInfo / StudyRead / SeriesBase).

        Production passes whatever `ctx.client.get_study()` returns — DTO
        shapes, not ORM rows. The other tests in this class build ORM
        instances because the factory is convenient; this one validates the
        DTO contract that `build_context` is actually fed at runtime.
        """
        from datetime import UTC, datetime

        from clarinet.models.patient import PatientInfo
        from clarinet.models.study import SeriesBase, StudyRead

        patient = PatientInfo(id="DTO_PAT", name="Test", auto_id=801)
        series = SeriesBase(
            series_uid="1.2.8004.1.1",
            series_number=1,
            modality="CT",
            anon_uid="ANON_DTO_SERIES",
            study_uid="1.2.8004.1",
        )
        study = StudyRead(
            study_uid="1.2.8004.1",
            date=datetime.now(UTC).date(),
            modalities_in_study="CT",
            anon_uid="ANON_DTO_STUDY",
            patient_id="DTO_PAT",
            patient=patient,
            series=[series],
        )

        # No dcm_anon dir on disk → False, but the call must complete without
        # AttributeError on any DTO field `build_context` reads.
        assert _has_dcm_anon(tmp_path, patient, study, series) is False


class TestFilterSeriesToFetch:
    """Tests for the fetch/skip partitioning of C-FIND results.

    Covers the dcm_anon shortcut over ``ctx.client.get_study()`` (degradation
    paths: 404, missing series in Study) and the disk-cache skip now driven by
    the dimsechord SQLite index (``cache.series_cached``).
    """

    @pytest.fixture
    def cache(self, tmp_path: Path) -> Iterator[DicomCache]:
        c = _open_cache(tmp_path)
        try:
            yield c
        finally:
            c.shutdown()

    @staticmethod
    def _mock_study_read(series_uids: list[str]) -> MagicMock:
        """Build a ``StudyRead``-shaped mock with patient + listed series.

        Uses ``spec=`` against the real model classes so a typo on the
        production attribute access (e.g. ``study_read.serieses``) fails the
        test instead of silently returning another MagicMock.
        """
        from clarinet.models.patient import PatientInfo
        from clarinet.models.study import SeriesBase, StudyRead

        patient = MagicMock(spec=PatientInfo)
        series_mocks: list[MagicMock] = []
        for suid in series_uids:
            s = MagicMock(spec=SeriesBase)
            s.series_uid = suid
            series_mocks.append(s)
        sr = MagicMock(spec=StudyRead)
        sr.patient = patient
        sr.series = series_mocks
        sr.study_uid = "STUDY1"
        return sr

    @pytest.mark.asyncio
    async def test_study_404_fetches_all_without_anon_check(
        self, tmp_path: Path, cache: DicomCache
    ) -> None:
        """API 404 (race vs C-FIND on PACS) → every series goes to fetch."""
        client = AsyncMock()
        client.get_study = AsyncMock(
            side_effect=ClarinetAPIError("study not found", status_code=404)
        )

        has_anon_calls: list[tuple] = []

        def _spy(*args, **kwargs):
            has_anon_calls.append(args)
            return False

        with patch("clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon", _spy):
            to_fetch, skipped_cached, skipped_anon = await _filter_series_to_fetch(
                series_uids=["SER1", "SER2"],
                storage_path=tmp_path,
                cache=cache,
                study_uid="STUDY1",
                skip_if_anon=True,
                client=client,
            )

        assert to_fetch == ["SER1", "SER2"]
        assert skipped_cached == 0
        assert skipped_anon == 0
        assert has_anon_calls == [], "dcm_anon shortcut must be bypassed on API failure"
        client.get_study.assert_awaited_once_with("STUDY1")

    @pytest.mark.asyncio
    async def test_non_404_api_error_propagates(self, tmp_path: Path, cache: DicomCache) -> None:
        """5xx / auth misconfig / non-404 errors must NOT silently degrade.

        Swallowing those would mask config drift (e.g. broken service_token)
        behind gigabytes of redundant PACS traffic. Retry/DLQ should see them.
        """
        client = AsyncMock()
        client.get_study = AsyncMock(
            side_effect=ClarinetAPIError("internal server error", status_code=500)
        )

        with (
            patch(
                "clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon",
                MagicMock(return_value=False),
            ),
            pytest.raises(ClarinetAPIError, match="internal server error"),
        ):
            await _filter_series_to_fetch(
                series_uids=["SER1"],
                storage_path=tmp_path,
                cache=cache,
                study_uid="STUDY1",
                skip_if_anon=True,
                client=client,
            )

    @pytest.mark.asyncio
    async def test_skip_if_anon_disabled_skips_api_call(
        self, tmp_path: Path, cache: DicomCache
    ) -> None:
        """``skip_if_anon=False`` → no ``client.get_study()`` call at all."""
        client = AsyncMock()
        client.get_study = AsyncMock()

        to_fetch, skipped_cached, skipped_anon = await _filter_series_to_fetch(
            series_uids=["SER1", "SER2"],
            storage_path=tmp_path,
            cache=cache,
            study_uid="STUDY1",
            skip_if_anon=False,
            client=client,
        )

        assert to_fetch == ["SER1", "SER2"]
        assert skipped_cached == 0
        assert skipped_anon == 0
        client.get_study.assert_not_called()

    @pytest.mark.asyncio
    async def test_series_missing_from_study_goes_to_fetch(
        self, tmp_path: Path, cache: DicomCache
    ) -> None:
        """C-FIND series absent from ``StudyRead.series`` → fetched, no anon check."""
        client = AsyncMock()
        client.get_study = AsyncMock(return_value=self._mock_study_read(series_uids=["SER1"]))

        seen_series: list[str] = []

        def _spy(_storage, _patient, _study, series):
            seen_series.append(series.series_uid)
            return False

        with patch("clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon", _spy):
            to_fetch, _, skipped_anon = await _filter_series_to_fetch(
                series_uids=["SER1", "SER2"],
                storage_path=tmp_path,
                cache=cache,
                study_uid="STUDY1",
                skip_if_anon=True,
                client=client,
            )

        # SER2 is not in StudyRead.series → fetched directly, _has_dcm_anon never
        # called for it. SER1 is in StudyRead.series → anon check ran (said False).
        assert to_fetch == ["SER1", "SER2"]
        assert skipped_anon == 0
        assert seen_series == ["SER1"]

    @pytest.mark.asyncio
    async def test_anon_shortcut_skips_listed_series(
        self, tmp_path: Path, cache: DicomCache
    ) -> None:
        """When ``_has_dcm_anon`` says True for a series, it's counted in ``skipped_anon``."""
        client = AsyncMock()
        client.get_study = AsyncMock(
            return_value=self._mock_study_read(series_uids=["SER1", "SER2"])
        )

        def _yes_for_ser1(_storage, _patient, _study, series):
            return series.series_uid == "SER1"

        with patch(
            "clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon",
            _yes_for_ser1,
        ):
            to_fetch, _, skipped_anon = await _filter_series_to_fetch(
                series_uids=["SER1", "SER2"],
                storage_path=tmp_path,
                cache=cache,
                study_uid="STUDY1",
                skip_if_anon=True,
                client=client,
            )

        assert to_fetch == ["SER2"]
        assert skipped_anon == 1

    @pytest.mark.asyncio
    async def test_index_cache_skip_takes_precedence_over_anon_check(
        self, tmp_path: Path, cache: DicomCache
    ) -> None:
        """Series present in the disk index must be detected first — no anon check."""
        # Seed the SQLite index so series_cached(STUDY1, SER1) is True.
        cache.write_instance("STUDY1", "SER1", "SOP1", _make_dataset("SER1", "SOP1", "STUDY1"))

        client = AsyncMock()
        client.get_study = AsyncMock(return_value=self._mock_study_read(series_uids=["SER1"]))

        anon_called = False

        def _spy(*args, **kwargs):
            nonlocal anon_called
            anon_called = True
            return True

        with patch("clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon", _spy):
            to_fetch, skipped_cached, skipped_anon = await _filter_series_to_fetch(
                series_uids=["SER1"],
                storage_path=tmp_path,
                cache=cache,
                study_uid="STUDY1",
                skip_if_anon=True,
                client=client,
            )

        assert to_fetch == []
        assert skipped_cached == 1
        assert skipped_anon == 0
        assert anon_called is False


class TestPrefetchDicomWebImpl:
    """Tests for the core prefetch logic."""

    @pytest.fixture(autouse=True)
    def _stub_has_dcm_anon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default to ``_has_dcm_anon`` → False.

        Keeps tests independent of template/filesystem rendering and focuses
        each case on the engine fetch branching. The dcm-anon-skip flow has
        its own override below.
        """

        def _no_anon(*args, **kwargs):
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

        holder = _patch_engine(monkeypatch, plan={})

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        mock_client.find_series.assert_awaited_once()
        # No series found → engine never built, nothing retrieved.
        assert "engine" not in holder

    @pytest.mark.asyncio
    async def test_skips_when_all_series_cached(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        # Pre-seed the SQLite index for both series.
        _seed_index(tmp_path, "STUDY1", "SER1", ["SOP1"])
        _seed_index(tmp_path, "STUDY1", "SER2", ["SOP2"])

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(
            return_value=[_series_result("SER1"), _series_result("SER2")]
        )
        holder = _patch_engine(monkeypatch, plan={})

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        # Fully covered → engine never built (no retrieve).
        assert "engine" not in holder

    @pytest.mark.asyncio
    async def test_skips_dcm_anon_by_default(self, tmp_path: Path, monkeypatch):
        """When ``_has_dcm_anon`` says True, no retrieve is attempted."""
        from clarinet.models.patient import PatientInfo
        from clarinet.models.study import SeriesBase, StudyRead

        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        def _yes_anon(*args, **kwargs):
            return True

        monkeypatch.setattr(
            "clarinet.services.pipeline.tasks.cache_dicomweb._has_dcm_anon",
            _yes_anon,
        )

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        # _filter_series_to_fetch looks SER1 up in StudyRead.series before
        # asking _has_dcm_anon — a series absent from the StudyRead would
        # short-circuit and head straight to fetch.
        series_obj = MagicMock(spec=SeriesBase)
        series_obj.series_uid = "SER1"
        study_read = MagicMock(spec=StudyRead)
        study_read.patient = MagicMock(spec=PatientInfo)
        study_read.series = [series_obj]
        ctx.client.get_study = AsyncMock(return_value=study_read)

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=[_series_result("SER1")])
        holder = _patch_engine(monkeypatch, plan={})

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        # dcm_anon covered → engine never built.
        assert "engine" not in holder
        # The API-driven anon-skip path must actually fire.
        ctx.client.get_study.assert_awaited_once_with("STUDY1")

    @pytest.mark.asyncio
    async def test_skip_if_anon_false_forces_fetch(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(
            patient_id="PAT001", study_uid="STUDY1", payload={"skip_if_anon": False}
        )

        # dcm_anon present but should be ignored.
        anon = tmp_path / "PAT001" / "STUDY1" / "SER1" / "dcm_anon"
        anon.mkdir(parents=True)
        (anon / "inst.dcm").write_bytes(b"fake")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=[_series_result("SER1")])
        holder = _patch_engine(monkeypatch, plan={"SER1": ["SOP-NEW"]})

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        engine = holder["engine"]
        # Whole study cold → one study-level association, not a per-series call.
        assert engine.study_calls == [["SER1"]]  # type: ignore[attr-defined]
        assert engine.calls == []  # type: ignore[attr-defined]
        # The fetched series landed in the shared SQLite index.
        assert _series_cached(tmp_path, "STUDY1", "SER1")

    @pytest.mark.asyncio
    async def test_all_missing_fetches_in_one_study_association(self, tmp_path: Path, monkeypatch):
        """A fully-cold study is pulled in ONE study-level association.

        Every discovered series is missing → the task takes the ``stream_study``
        branch (a single study-level C-GET/C-MOVE) rather than N per-series
        ``ensure_series`` calls — behavioural parity with the pre-refactor base
        (one study-level C-GET avoids N PACS associations).
        """
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(
            return_value=[_series_result("SER1"), _series_result("SER2")]
        )
        holder = _patch_engine(monkeypatch, plan={"SER1": ["SOP1"], "SER2": ["SOP2"]})

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        engine = holder["engine"]
        # ONE study-level association covering both series; no per-series loop.
        assert engine.study_calls == [["SER1", "SER2"]]  # type: ignore[attr-defined]
        assert engine.calls == []  # type: ignore[attr-defined]
        assert _series_cached(tmp_path, "STUDY1", "SER1")
        assert _series_cached(tmp_path, "STUDY1", "SER2")

    @pytest.mark.asyncio
    async def test_cold_study_partial_arrival_reports_missing_series(
        self, tmp_path: Path, monkeypatch
    ):
        """A study-level stream where SOME series never arrive must flag the
        no-shows — parity with the per-series branch — instead of reporting a
        clean success for a study that only partially landed.
        """
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(
            return_value=[_series_result("SER1"), _series_result("SER2")]
        )
        # SER1 streams an instance; SER2 never arrives (empty plan entry).
        holder = _patch_engine(monkeypatch, plan={"SER1": ["SOP1"], "SER2": []})

        mock_logger = MagicMock()
        monkeypatch.setattr("clarinet.services.pipeline.tasks.cache_dicomweb.logger", mock_logger)

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        engine = holder["engine"]
        # ONE study-level association over both series (cold-study branch) ...
        assert engine.study_calls == [["SER1", "SER2"]]  # type: ignore[attr-defined]
        assert engine.calls == []  # type: ignore[attr-defined]
        # ... SER1 arrived, SER2 did not.
        assert _series_cached(tmp_path, "STUDY1", "SER1")
        assert not _series_cached(tmp_path, "STUDY1", "SER2")
        # The partial failure was reported, naming only the missing series.
        assert mock_logger.error.called
        failure_msg = mock_logger.error.call_args.args[0]
        assert "SER2" in failure_msg
        assert "SER1" not in failure_msg

    @pytest.mark.asyncio
    async def test_partial_cache_fetches_only_missing(self, tmp_path: Path, monkeypatch):
        """Already-indexed series are skipped; only missing ones are retrieved."""
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        # SER1 already indexed.
        _seed_index(tmp_path, "STUDY1", "SER1", ["OLD"])

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(
            return_value=[_series_result("SER1"), _series_result("SER2")]
        )
        holder = _patch_engine(monkeypatch, plan={"SER2": ["SOP-NEW"]})

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)

        engine = holder["engine"]
        assert engine.calls == ["SER2"]  # type: ignore[attr-defined]
        assert _series_cached(tmp_path, "STUDY1", "SER2")

    @pytest.mark.asyncio
    async def test_zero_instances_raises(self, tmp_path: Path, monkeypatch):
        """A retrieve that returns no instances for any series is a hard failure."""
        monkeypatch.setattr("clarinet.settings.settings.storage_path", str(tmp_path))

        ctx = _build_ctx(tmp_path)
        msg = PipelineMessage(patient_id="PAT001", study_uid="STUDY1")

        mock_client = AsyncMock()
        mock_client.find_series = AsyncMock(return_value=[_series_result("SER1")])
        # Empty plan → the study-level stream yields no instances.
        _patch_engine(monkeypatch, plan={})

        with (
            patch("clarinet.services.dicom.DicomClient", return_value=mock_client),
            patch("clarinet.services.dicom.DicomNode"),
            pytest.raises(PipelineStepError, match="0 instances"),
        ):
            await _prefetch_dicom_web_impl(msg, ctx)
