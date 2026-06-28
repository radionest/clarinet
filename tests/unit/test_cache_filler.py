"""Unit tests for the CacheFiller adapter.

CacheFiller wraps dimsechord's DicomCache + PullEngine and re-adds the two
Clarinet-specific concerns dimsechord deliberately omits:
- a dcm_anon tier-0 (anonymized files served before ever touching the PACS)
- a preload progress store (TTLCache) for the SSE preload widget
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydicom import Dataset

from clarinet.services.dicomweb.filler import CacheFiller
from tests.utils.session import PassThroughSession


def _ds(sop: str) -> Dataset:
    d = Dataset()
    d.SOPInstanceUID = sop
    d.StudyInstanceUID = "1.2"
    d.SeriesInstanceUID = "1.2.3"
    return d


def _ds2(study: str, series: str, sop: str) -> Dataset:
    d = Dataset()
    d.SOPInstanceUID = sop
    d.StudyInstanceUID = study
    d.SeriesInstanceUID = series
    return d


def _mcs(n: int, study: str = "ST", series: str = "S") -> MagicMock:
    """A MemoryCachedSeries-like mock with ``n`` sized instances."""
    m = MagicMock()
    m.instances = {str(i): _ds2(study, series, str(i)) for i in range(n)}
    return m


def _make_filler(
    *,
    cache: MagicMock | None = None,
    engine: MagicMock | None = None,
    client: MagicMock | None = None,
    pacs: MagicMock | None = None,
    mode: str = "c-get",
    session_factory: object | None = None,
    storage_path: Path | None = None,
    **kw: object,
) -> CacheFiller:
    return CacheFiller(
        cache=cache or MagicMock(),
        engine=engine or MagicMock(),
        client=client or MagicMock(),
        pacs=pacs or MagicMock(),
        retrieve_mode=mode,
        session_factory=session_factory,  # type: ignore[arg-type]
        storage_path=storage_path or Path("/tmp"),
        **kw,  # type: ignore[arg-type]
    )


# --- ensure_series: dcm_anon tier-0 short-circuit + fall-through ---------


@pytest.mark.asyncio
async def test_ensure_series_serves_dcm_anon_without_engine(monkeypatch, tmp_path):
    cache = MagicMock()
    cache.get_series_from_memory.return_value = None
    sentinel = object()
    cache.put_series_to_memory.return_value = sentinel
    engine = MagicMock()
    engine.ensure_series = AsyncMock()

    filler = CacheFiller(
        cache=cache,
        engine=engine,
        client=MagicMock(),
        pacs=MagicMock(),
        retrieve_mode="c-get",
        session_factory=MagicMock(),
        storage_path=tmp_path,
    )

    anon_dir = tmp_path / "dcm_anon"
    anon_dir.mkdir()
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(return_value=anon_dir))
    monkeypatch.setattr(filler, "_read_dcm_files", staticmethod(lambda d: {"1.4": _ds("1.4")}))

    result = await filler.ensure_series("1.2", "1.2.3")

    assert result is sentinel
    cache.put_series_to_memory.assert_called_once()
    assert cache.put_series_to_memory.call_args.kwargs.get("disk_persisted") is True
    engine.ensure_series.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_series_falls_through_to_engine_on_anon_miss(monkeypatch, tmp_path):
    cache = MagicMock()
    cache.get_series_from_memory.return_value = None
    engine = MagicMock()
    engine.ensure_series = AsyncMock(return_value="ENGINE")
    filler = CacheFiller(
        cache=cache,
        engine=engine,
        client=MagicMock(),
        pacs=MagicMock(),
        retrieve_mode="c-move",
        session_factory=MagicMock(),
        storage_path=tmp_path,
    )
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(return_value=None))

    result = await filler.ensure_series("1.2", "1.2.3")

    assert result == "ENGINE"
    engine.ensure_series.assert_awaited_once_with("1.2", "1.2.3")


@pytest.mark.asyncio
async def test_ensure_series_memory_hit_skips_anon_and_engine(monkeypatch, tmp_path):
    """A memory hit returns immediately — no dcm_anon resolve, no engine."""
    cache = MagicMock()
    hit = object()
    cache.get_series_from_memory.return_value = hit
    engine = MagicMock()
    engine.ensure_series = AsyncMock()
    filler = _make_filler(cache=cache, engine=engine)
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", resolve)

    result = await filler.ensure_series("1.2", "1.2.3")

    assert result is hit
    resolve.assert_not_awaited()
    engine.ensure_series.assert_not_awaited()


# --- ensure_study: per-series loop (default c-get, the must-have) --------


@pytest.mark.asyncio
async def test_ensure_study_default_loops_per_series_and_aggregates_progress(monkeypatch, tmp_path):
    cache = MagicMock()
    cache.get_series_from_memory.return_value = None
    engine = MagicMock()
    series_objs = {"S1": _mcs(2), "S2": _mcs(3)}
    engine.ensure_series = AsyncMock(side_effect=lambda study, ser: series_objs[ser])

    filler = _make_filler(cache=cache, engine=engine, mode="c-get")
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(return_value=None))

    progress: list[tuple[int, int | None]] = []
    result = await filler.ensure_study(
        "ST", ["S1", "S2"], on_progress=lambda r, t: progress.append((r, t))
    )

    assert set(result) == {"S1", "S2"}
    assert engine.ensure_series.await_count == 2
    # Aggregate instance counts: after S1 -> 2, after S2 -> 2 + 3 = 5
    assert progress == [(2, None), (5, None)]


# --- ensure_study: c-get-study (single study-level C-GET with progress) --


@pytest.mark.asyncio
async def test_ensure_study_cget_study_single_association(monkeypatch, tmp_path):
    cache = MagicMock()
    cache.get_series_from_memory.return_value = None
    cache.load_series_from_disk.return_value = None
    cache.put_series_to_memory.side_effect = lambda study, series, instances, **kw: MagicMock(
        instances=instances
    )
    client = MagicMock()
    cget = MagicMock()
    cget.instances = {"a": _ds2("ST", "S1", "a"), "b": _ds2("ST", "S2", "b")}
    cget.num_completed = 2
    cget.status = 0x0000
    client.get_study_to_memory = AsyncMock(return_value=cget)

    filler = _make_filler(cache=cache, client=client, mode="c-get-study")
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(return_value=None))

    progress_cb = MagicMock()
    result = await filler.ensure_study("ST", ["S1", "S2"], on_progress=progress_cb)

    # One study-level association; progress callback forwarded to the client.
    client.get_study_to_memory.assert_awaited_once()
    assert client.get_study_to_memory.await_args.kwargs["on_progress"] is progress_cb
    assert set(result) == {"S1", "S2"}
    # Two series put to memory, one tee per instance.
    assert cache.put_series_to_memory.call_count == 2
    assert cache.schedule_tee.call_count == 2


@pytest.mark.asyncio
async def test_ensure_study_cget_study_preserves_dcm_anon_over_raw(monkeypatch, tmp_path):
    """A series resolved from dcm_anon must not be overwritten by raw PACS data."""
    cache = MagicMock()
    cache.get_series_from_memory.return_value = None
    cache.load_series_from_disk.return_value = None
    anon_entry = MagicMock(instances={"a": _ds2("ST", "S1", "a")})

    def _put(study, series, instances, **kw):
        return anon_entry if kw.get("disk_persisted") else MagicMock(instances=instances)

    cache.put_series_to_memory.side_effect = _put
    client = MagicMock()
    cget = MagicMock()
    # PACS returns raw copies of BOTH S1 (already anon) and S2 (genuinely missing).
    cget.instances = {"a2": _ds2("ST", "S1", "a2"), "b": _ds2("ST", "S2", "b")}
    cget.num_completed = 2
    cget.status = 0x0000
    client.get_study_to_memory = AsyncMock(return_value=cget)

    filler = _make_filler(cache=cache, client=client, mode="c-get-study")

    # S1 resolves from dcm_anon; S2 misses everywhere.
    async def _resolve(study, series):
        return tmp_path / "anon" if series == "S1" else None

    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(side_effect=_resolve))
    monkeypatch.setattr(
        filler, "_read_dcm_files", staticmethod(lambda d: {"a": _ds2("ST", "S1", "a")})
    )

    result = await filler.ensure_study("ST", ["S1", "S2"], on_progress=None)

    # S1 keeps the dcm_anon entry; only S2 is tee'd from the raw C-GET.
    assert result["S1"] is anon_entry
    assert cache.schedule_tee.call_count == 1  # only S2's single instance


# --- ensure_study: c-move-study (stream_study counting arrivals) ---------


@pytest.mark.asyncio
async def test_ensure_study_cmove_streams_and_counts_arrivals(monkeypatch, tmp_path):
    cache = MagicMock()
    cache.get_series_from_memory.return_value = None
    cache.load_series_from_disk.return_value = None
    cache.put_series_to_memory.side_effect = lambda study, series, instances, **kw: MagicMock(
        instances=instances
    )
    engine = MagicMock()

    async def _stream(study_uid, series_uids):
        yield _ds2("ST", "S1", "a")
        yield _ds2("ST", "S1", "b")
        yield _ds2("ST", "S2", "c")

    engine.stream_study = _stream

    filler = _make_filler(cache=cache, engine=engine, mode="c-move-study")
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(return_value=None))

    progress: list[int] = []
    result = await filler.ensure_study(
        "ST", ["S1", "S2"], on_progress=lambda r, t: progress.append(r)
    )

    assert set(result) == {"S1", "S2"}
    assert progress == [1, 2, 3]  # one tick per arriving instance
    assert cache.put_series_to_memory.call_count == 2  # grouped into 2 series


# --- delegating methods + preload store ----------------------------------


@pytest.mark.asyncio
async def test_read_instance_and_build_zip_delegate_to_cache(tmp_path):
    cache = MagicMock()
    cache.read_instance.return_value = "DS"
    cache.build_series_zip.return_value = 7
    filler = _make_filler(cache=cache)

    assert await filler.read_instance("ST", "SE", "SOP") == "DS"
    cache.read_instance.assert_called_once_with("ST", "SE", "SOP")
    out = MagicMock()
    assert filler.build_series_zip("cached", out) == 7
    cache.build_series_zip.assert_called_once_with("cached", out)


@pytest.mark.asyncio
async def test_read_instance_prefers_dcm_anon_over_disk_index(monkeypatch, tmp_path):
    """PHI guard: a dcm_anon instance is served over a disk-index entry.

    A series evicted from memory between ``ensure_series`` and ``read_instance``
    must re-serve the *anonymized* SOP, never a raw copy tee'd to the disk index
    under the same UID.
    """
    cache = MagicMock()
    cache.read_instance.return_value = _ds("RAW")  # what the disk index would serve
    filler = _make_filler(cache=cache, session_factory=MagicMock(), storage_path=tmp_path)

    anon_dir = tmp_path / "dcm_anon"
    anon_dir.mkdir()
    anon_ds = _ds("ANON")
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(return_value=anon_dir))
    monkeypatch.setattr(filler, "_read_single_dcm", lambda d, sop: anon_ds)

    result = await filler.read_instance("ST", "SE", "ANON")

    assert result is anon_ds
    cache.read_instance.assert_not_called()


@pytest.mark.asyncio
async def test_read_instance_falls_back_to_disk_index_when_anon_misses(monkeypatch, tmp_path):
    """dcm_anon dir resolved but the SOP file is absent → the disk index serves it."""
    cache = MagicMock()
    cache.read_instance.return_value = _ds("DISK")
    filler = _make_filler(cache=cache, session_factory=MagicMock(), storage_path=tmp_path)

    anon_dir = tmp_path / "dcm_anon"
    anon_dir.mkdir()
    monkeypatch.setattr(filler, "_resolve_dcm_anon_dir", AsyncMock(return_value=anon_dir))
    monkeypatch.setattr(filler, "_read_single_dcm", lambda d, sop: None)

    result = await filler.read_instance("ST", "SE", "MISSING")

    assert str(result.SOPInstanceUID) == "DISK"
    cache.read_instance.assert_called_once_with("ST", "SE", "MISSING")


def test_evict_methods_delegate_to_cache(tmp_path):
    cache = MagicMock()
    cache.evict_expired.return_value = 3
    cache.evict_by_size.return_value = 5
    filler = _make_filler(cache=cache)
    assert filler.evict_expired() == 3
    assert filler.evict_by_size() == 5


@pytest.mark.asyncio
async def test_shutdown_flushes_then_shuts_down(tmp_path):
    cache = MagicMock()
    filler = _make_filler(cache=cache)
    await filler.shutdown()
    cache.flush_pending_writes.assert_called_once()
    cache.shutdown.assert_called_once()


def test_preload_progress_set_get_and_miss(tmp_path):
    filler = _make_filler()
    assert filler.get_preload_progress("unknown") is None
    filler.set_preload_progress("t1", {"status": "fetching", "received": 3})
    assert filler.get_preload_progress("t1") == {"status": "fetching", "received": 3}


# --- dcm_anon resolver (DB seam) — ported from test_dicomweb_cache.py -----


class _SessionFactory:
    def __init__(self, session) -> None:
        self._session = session

    def __call__(self):
        return PassThroughSession(self._session)


class TestResolveDcmAnonDir:
    """DB-aware dcm_anon lookup via ``settings.disk_path_template``.

    Ported from ``tests/test_dicomweb_cache.py::TestResolveDcmAnonDir`` to target
    ``CacheFiller._resolve_dcm_anon_dir`` (the dcm_anon machinery moved here).
    """

    @pytest.mark.asyncio
    async def test_resolves_existing_dir_via_db(
        self, tmp_path: Path, test_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import UTC, datetime

        from clarinet.files._storage import build_context, render_working_folder
        from clarinet.models.base import DicomQueryLevel
        from clarinet.models.study import Series, Study
        from tests.utils.factories import make_patient

        patient = make_patient("RESOLVE_PAT_01", "Resolve", auto_id=303)
        test_session.add(patient)
        await test_session.commit()
        study = Study(
            patient_id="RESOLVE_PAT_01",
            study_uid="1.2.7001.1",
            date=datetime.now(UTC).date(),
            modalities_in_study="CT",
            anon_uid="2.25.701",
        )
        test_session.add(study)
        await test_session.commit()
        series = Series(
            study_uid="1.2.7001.1",
            series_uid="1.2.7001.1.1",
            series_number=1,
            modality="CT",
            anon_uid="2.25.701.1",
        )
        test_session.add(series)
        await test_session.commit()

        monkeypatch.setattr(
            "clarinet.files._storage.settings.disk_path_template",
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
        )
        monkeypatch.setattr(
            "clarinet.files._storage.settings.anon_per_study_patient_id",
            False,
        )
        monkeypatch.setattr(
            "clarinet.files._storage.settings.anon_id_prefix",
            "CLARINET",
        )
        ctx = build_context(patient=patient, study=study, series=series)
        series_dir = render_working_folder(
            "{anon_patient_id}/{anon_study_uid}/{anon_series_uid}",
            DicomQueryLevel.SERIES,
            ctx,
            tmp_path,
        )
        expected_dcm_anon = series_dir / "dcm_anon"
        expected_dcm_anon.mkdir(parents=True)
        (expected_dcm_anon / "x.dcm").write_bytes(b"placeholder")

        filler = _make_filler(storage_path=tmp_path, session_factory=_SessionFactory(test_session))
        resolved = await filler._resolve_dcm_anon_dir("1.2.7001.1", "1.2.7001.1.1")
        assert resolved == expected_dcm_anon

    @pytest.mark.asyncio
    async def test_returns_none_when_db_missing(self, tmp_path: Path, test_session) -> None:
        """Unknown series_uid → None, no exception, result cached."""
        filler = _make_filler(storage_path=tmp_path, session_factory=_SessionFactory(test_session))
        assert await filler._resolve_dcm_anon_dir("does.not.exist", "ne.either") is None
        assert await filler._resolve_dcm_anon_dir("does.not.exist", "ne.either") is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_session_factory(self, tmp_path: Path) -> None:
        """No session factory → dcm_anon tier disabled (safe fallback)."""
        filler = _make_filler(storage_path=tmp_path, session_factory=None)
        assert await filler._resolve_dcm_anon_dir("any", "any") is None

    @pytest.mark.asyncio
    async def test_negative_entry_expires_via_ttl(self, tmp_path: Path, test_session) -> None:
        """Negative result expires after TTL — guards against permanent masking
        of "anonymize-after-first-read" races."""
        filler = _make_filler(
            storage_path=tmp_path,
            session_factory=_SessionFactory(test_session),
            dcm_anon_path_cache_ttl_seconds=1,
        )
        assert await filler._resolve_dcm_anon_dir("no.such", "no.such.1") is None
        assert "no.such/no.such.1" in filler._dcm_anon_path_cache

        ttl_cache = filler._dcm_anon_path_cache
        ttl_cache.expire(ttl_cache.timer() + ttl_cache.ttl + 1)
        assert "no.such/no.such.1" not in filler._dcm_anon_path_cache

    @pytest.mark.asyncio
    async def test_returns_none_on_study_series_mismatch(
        self, tmp_path: Path, test_session
    ) -> None:
        """Series exists under a different Study → None (guard against
        inconsistent context resolution)."""
        from datetime import UTC, datetime

        from clarinet.models.study import Series, Study
        from tests.utils.factories import make_patient

        patient = make_patient("MISMATCH_PAT", "Mismatch", auto_id=909)
        test_session.add(patient)
        await test_session.commit()
        for uid, anon in (("STUDY.A", "2.25.A"), ("STUDY.B", "2.25.B")):
            test_session.add(
                Study(
                    patient_id="MISMATCH_PAT",
                    study_uid=uid,
                    date=datetime.now(UTC).date(),
                    modalities_in_study="CT",
                    anon_uid=anon,
                )
            )
        await test_session.commit()
        test_session.add(
            Series(
                study_uid="STUDY.A",
                series_uid="SER.X",
                series_number=1,
                modality="CT",
                anon_uid="2.25.A.1",
            )
        )
        await test_session.commit()

        filler = _make_filler(storage_path=tmp_path, session_factory=_SessionFactory(test_session))
        assert await filler._resolve_dcm_anon_dir("STUDY.B", "SER.X") is None
        assert "STUDY.B/SER.X" in filler._dcm_anon_path_cache

    @pytest.mark.asyncio
    async def test_invalidate_dcm_anon_path_drops_entry(self, tmp_path: Path, test_session) -> None:
        """``invalidate_dcm_anon_path`` removes a single cached entry."""
        filler = _make_filler(storage_path=tmp_path, session_factory=_SessionFactory(test_session))
        assert await filler._resolve_dcm_anon_dir("study.x", "series.x") is None
        assert "study.x/series.x" in filler._dcm_anon_path_cache

        filler.invalidate_dcm_anon_path("study.x", "series.x")
        assert "study.x/series.x" not in filler._dcm_anon_path_cache
        # Unknown key is a no-op (no KeyError).
        filler.invalidate_dcm_anon_path("never.cached", "neither.have.you")
