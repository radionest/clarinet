"""Unit tests for DicomWebCache.

Tests cover:
- Memory hit returns entry without disk access
- TTL expiration causes cache miss
- LRU eviction when maxsize reached
- shutdown() clears memory cache
- Background disk write is safe after eviction
- Disk eviction: expired entries removed, fresh preserved
- Size-based eviction: oldest removed when over limit
"""

import asyncio
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from cachetools import TTLCache

from clarinet.services.dicomweb.cache import DicomWebCache
from clarinet.services.dicomweb.models import MemoryCachedSeries
from tests.conftest import create_disk_series


def _make_instances(count: int = 3) -> dict[str, Any]:
    """Create a dict of mock DICOM datasets keyed by SOPInstanceUID."""
    instances: dict[str, Any] = {}
    for i in range(count):
        ds = MagicMock()
        ds.SOPInstanceUID = f"1.2.3.{i}"
        instances[f"1.2.3.{i}"] = ds
    return instances


@pytest.fixture
def tmp_cache_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for cache storage."""
    return tmp_path / "dicomweb_cache"


@pytest_asyncio.fixture
async def cache(tmp_cache_dir: Path) -> AsyncGenerator[DicomWebCache, None]:
    """Create a DicomWebCache with short TTL for testing."""
    c = DicomWebCache(
        base_dir=tmp_cache_dir,
        ttl_hours=24,
        max_size_gb=1.0,
        memory_ttl_minutes=30,
        memory_max_entries=50,
    )
    yield c
    await c.shutdown()


class TestMemoryHit:
    """Test that memory cache hit returns entry without disk access."""

    def test_put_and_get_from_memory(self, cache: DicomWebCache) -> None:
        """Storing a series in memory should make it retrievable."""
        instances = _make_instances()
        cache._put_to_memory("study1", "series1", instances, disk_persisted=False)

        result = cache._get_from_memory("study1", "series1")
        assert result is not None
        assert result.study_uid == "study1"
        assert result.series_uid == "series1"
        assert len(result.instances) == 3

    def test_memory_miss_returns_none(self, cache: DicomWebCache) -> None:
        """Querying a non-existent key should return None."""
        result = cache._get_from_memory("study1", "nonexistent")
        assert result is None

    def test_memory_hit_does_not_touch_disk(self, cache: DicomWebCache) -> None:
        """Memory hit should not invoke _load_from_disk."""
        instances = _make_instances()
        cache._put_to_memory("study1", "series1", instances)

        with patch.object(cache, "_load_from_disk") as mock_disk:
            result = cache._get_from_memory("study1", "series1")
            assert result is not None
            mock_disk.assert_not_called()


class TestTTLExpiration:
    """Test that expired entries are not returned."""

    def test_expired_entry_returns_none(self, tmp_cache_dir: Path) -> None:
        """After TTL expires, _get_from_memory should return None."""
        # Create cache with very short TTL (1 second expressed as minutes)
        cache = DicomWebCache(
            base_dir=tmp_cache_dir,
            memory_ttl_minutes=0,  # 0 minutes = 0 seconds TTL
            memory_max_entries=50,
        )
        # Manually insert with TTLCache's internal TTL of 0 — entry expires immediately
        # Instead, create a cache with 1-second TTL by manipulating the TTLCache directly
        cache._memory_cache = TTLCache(maxsize=50, ttl=0.01)

        instances = _make_instances()
        cache._put_to_memory("study1", "series1", instances)

        # Entry should be present immediately
        assert cache._get_from_memory("study1", "series1") is not None

        # Wait for TTL to expire

        time.sleep(0.02)

        # Now it should be gone
        assert cache._get_from_memory("study1", "series1") is None

    def test_fresh_entry_is_returned(self, cache: DicomWebCache) -> None:
        """Within TTL, _get_from_memory should return the entry."""
        instances = _make_instances()
        cache._put_to_memory("study1", "series1", instances)

        result = cache._get_from_memory("study1", "series1")
        assert result is not None
        assert result.series_uid == "series1"


class TestLRUEviction:
    """Test LRU eviction when cache reaches maxsize."""

    def test_oldest_entry_evicted(self, tmp_cache_dir: Path) -> None:
        """When cache reaches maxsize, the least-recently-used entry is evicted."""
        cache = DicomWebCache(
            base_dir=tmp_cache_dir,
            memory_max_entries=3,
        )

        instances = _make_instances(1)

        # Fill cache with 3 entries
        cache._put_to_memory("study1", "series_old", instances)
        cache._put_to_memory("study1", "series_mid", instances)
        cache._put_to_memory("study1", "series_new", instances)

        assert len(cache._memory_cache) == 3

        # Add a 4th — should evict series_old (LRU)
        cache._put_to_memory("study1", "series_4th", instances)

        assert len(cache._memory_cache) == 3
        assert cache._get_from_memory("study1", "series_old") is None
        assert cache._get_from_memory("study1", "series_mid") is not None
        assert cache._get_from_memory("study1", "series_new") is not None
        assert cache._get_from_memory("study1", "series_4th") is not None

    def test_accessing_entry_prevents_eviction(self, tmp_cache_dir: Path) -> None:
        """Accessing an entry should update its LRU position, preventing eviction."""
        cache = DicomWebCache(
            base_dir=tmp_cache_dir,
            memory_max_entries=3,
        )

        instances = _make_instances(1)

        cache._put_to_memory("study1", "series_a", instances)
        cache._put_to_memory("study1", "series_b", instances)
        cache._put_to_memory("study1", "series_c", instances)

        # Access series_a to make it recently used
        cache._get_from_memory("study1", "series_a")

        # Add series_d — should evict series_b (now the LRU)
        cache._put_to_memory("study1", "series_d", instances)

        assert cache._get_from_memory("study1", "series_a") is not None
        assert cache._get_from_memory("study1", "series_b") is None
        assert cache._get_from_memory("study1", "series_c") is not None
        assert cache._get_from_memory("study1", "series_d") is not None


class TestShutdown:
    """Test shutdown clears memory cache."""

    @pytest.mark.asyncio
    async def test_shutdown_clears_memory(self, cache: DicomWebCache) -> None:
        """shutdown() should clear all memory cache entries."""
        instances = _make_instances()
        cache._put_to_memory("study1", "series1", instances)
        cache._put_to_memory("study1", "series2", instances)

        assert len(cache._memory_cache) == 2

        await cache.shutdown()

        assert len(cache._memory_cache) == 0

    @pytest.mark.asyncio
    async def test_shutdown_clears_locks(self, cache: DicomWebCache) -> None:
        """shutdown() should clear per-series locks."""
        cache._get_lock("study1", "series1")
        assert len(cache._locks) == 1

        await cache.shutdown()

        assert len(cache._locks) == 0


class TestBackgroundDiskWriteSafety:
    """Test that LRU eviction doesn't break background disk writes."""

    @pytest.mark.asyncio
    async def test_eviction_does_not_break_disk_write(self, tmp_cache_dir: Path) -> None:
        """Background disk write should succeed even if entry was evicted from memory.

        The disk write receives instances directly, not from the cache.
        """
        cache = DicomWebCache(
            base_dir=tmp_cache_dir,
            memory_max_entries=2,
        )

        instances = _make_instances(1)

        # Fill cache
        cache._put_to_memory("study1", "series1", instances)
        cache._put_to_memory("study1", "series2", instances)

        # Simulate background disk write for series1 with direct reference
        # (this mirrors how ensure_series_cached passes result.instances directly)
        with patch.object(cache, "_write_to_disk") as mock_write:
            # Evict series1 by adding series3
            cache._put_to_memory("study1", "series3", instances)
            assert cache._get_from_memory("study1", "series1") is None

            # Disk write should still work — it uses the instances reference, not cache
            await cache._write_to_disk_background("study1", "series1", instances)
            mock_write.assert_called_once_with("study1", "series1", instances)

    @pytest.mark.asyncio
    async def test_disk_write_marks_persisted_if_still_in_cache(self, cache: DicomWebCache) -> None:
        """If entry is still in memory after disk write, it should be marked as persisted."""
        instances = _make_instances()
        cache._put_to_memory("study1", "series1", instances, disk_persisted=False)

        with patch.object(cache, "_write_to_disk"):
            await cache._write_to_disk_background("study1", "series1", instances)

        cached = cache._get_from_memory("study1", "series1")
        assert cached is not None
        assert cached.disk_persisted is True

    @pytest.mark.asyncio
    async def test_disk_write_skips_marking_if_evicted(self, tmp_cache_dir: Path) -> None:
        """If entry was evicted before disk write completes, no error should occur."""
        cache = DicomWebCache(
            base_dir=tmp_cache_dir,
            memory_max_entries=1,
        )

        instances = _make_instances()
        cache._put_to_memory("study1", "series1", instances)

        # Evict series1 by adding series2
        cache._put_to_memory("study1", "series2", instances)
        assert cache._get_from_memory("study1", "series1") is None

        # Disk write for evicted entry should not raise
        with patch.object(cache, "_write_to_disk"):
            await cache._write_to_disk_background("study1", "series1", instances)


class TestLoadFromDiskNoTTL:
    """Read-time does not evict — disk cache lifecycle is cleanup's job.

    DICOM data on the PACS is immutable, so ``_load_from_disk`` returns any
    present entry regardless of how old the marker is. Physical removal
    happens only via ``evict_expired`` / ``evict_by_size`` in the background
    cleanup service.
    """

    def test_ancient_entry_is_returned(self, tmp_cache_dir: Path) -> None:
        """A week-old marker must still be a cache hit."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=1)

        create_disk_series(
            tmp_cache_dir,
            "study1",
            "series_old",
            cached_at=time.time() - 7 * 86400,
        )

        mock_ds = MagicMock()
        mock_ds.SOPInstanceUID = "1.2.3"
        with patch("clarinet.services.dicomweb.cache.pydicom.dcmread", return_value=mock_ds):
            result = cache._load_from_disk("study1", "series_old")

        assert result is not None
        assert "1.2.3" in result
        # Files are still on disk
        assert (tmp_cache_dir / "study1" / "series_old").exists()

    def test_cleanup_still_removes_ancient_entry(self, tmp_cache_dir: Path) -> None:
        """Background cleanup remains the sole lifecycle authority."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=1)

        create_disk_series(
            tmp_cache_dir,
            "study1",
            "series_old",
            cached_at=time.time() - 7 * 86400,
        )

        # Read-path does not touch the files, regardless of age
        mock_ds = MagicMock()
        mock_ds.SOPInstanceUID = "1.2.3"
        with patch("clarinet.services.dicomweb.cache.pydicom.dcmread", return_value=mock_ds):
            assert cache._load_from_disk("study1", "series_old") is not None
        assert (tmp_cache_dir / "study1" / "series_old").exists()

        # Cleanup is what actually removes
        removed = cache.evict_expired()
        assert removed == 1
        assert not (tmp_cache_dir / "study1" / "series_old").exists()


class TestEvictExpired:
    """Test evict_expired() removes expired entries and preserves fresh ones."""

    def test_expired_removed(self, tmp_cache_dir: Path) -> None:
        """Expired series dirs should be removed."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=1)

        # Create an expired entry (2 hours old)
        create_disk_series(tmp_cache_dir, "study1", "series_old", cached_at=time.time() - 7200)

        removed = cache.evict_expired()
        assert removed == 1
        assert not (tmp_cache_dir / "study1" / "series_old").exists()

    def test_fresh_preserved(self, tmp_cache_dir: Path) -> None:
        """Fresh series dirs should not be removed."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=24)

        create_disk_series(tmp_cache_dir, "study1", "series_fresh", cached_at=time.time())

        removed = cache.evict_expired()
        assert removed == 0
        assert (tmp_cache_dir / "study1" / "series_fresh").exists()

    def test_empty_study_dirs_cleaned(self, tmp_cache_dir: Path) -> None:
        """After removing all series in a study, the empty study dir should be removed."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=1)

        create_disk_series(tmp_cache_dir, "study1", "series_old", cached_at=time.time() - 7200)

        cache.evict_expired()
        assert not (tmp_cache_dir / "study1").exists()

    def test_no_base_dir_returns_zero(self, tmp_cache_dir: Path) -> None:
        """If the base dir doesn't exist, should return 0."""
        cache = DicomWebCache(base_dir=tmp_cache_dir / "nonexistent", ttl_hours=1)
        assert cache.evict_expired() == 0


class TestEvictBySize:
    """Test evict_by_size() removes oldest entries when over the size limit."""

    def test_oldest_removed_when_over_limit(self, tmp_cache_dir: Path) -> None:
        """When total size exceeds max, oldest entries should be removed first."""
        # Max size = 2KB, each entry ~1KB
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=24, max_size_gb=0)
        cache._max_size_bytes = 2048  # Override for testing

        create_disk_series(
            tmp_cache_dir, "study1", "series_oldest", cached_at=100.0, file_size=1024
        )
        create_disk_series(
            tmp_cache_dir, "study1", "series_middle", cached_at=200.0, file_size=1024
        )
        create_disk_series(
            tmp_cache_dir, "study1", "series_newest", cached_at=300.0, file_size=1024
        )

        removed = cache.evict_by_size()
        # Should remove oldest entries until under 2048 bytes
        assert removed >= 1
        # Newest should survive
        assert (tmp_cache_dir / "study1" / "series_newest").exists()
        # Oldest should be gone
        assert not (tmp_cache_dir / "study1" / "series_oldest").exists()

    def test_no_op_when_under_limit(self, tmp_cache_dir: Path) -> None:
        """When total size is under the limit, nothing should be removed."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=24, max_size_gb=10.0)

        create_disk_series(
            tmp_cache_dir, "study1", "series1", cached_at=time.time(), file_size=1024
        )

        removed = cache.evict_by_size()
        assert removed == 0

    def test_get_directory_size_accuracy(self, tmp_cache_dir: Path) -> None:
        """_get_directory_size should return the sum of all file sizes."""
        series_dir = tmp_cache_dir / "study1" / "series1"
        series_dir.mkdir(parents=True)

        (series_dir / "a.dcm").write_bytes(b"\x00" * 500)
        (series_dir / "b.dcm").write_bytes(b"\x00" * 300)

        total = DicomWebCache._get_directory_size(series_dir)
        assert total == 800

    def test_empty_study_dirs_cleaned_after_size_eviction(self, tmp_cache_dir: Path) -> None:
        """Empty study directories should be removed after size-based eviction."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=24, max_size_gb=0)
        cache._max_size_bytes = 512

        create_disk_series(
            tmp_cache_dir, "study_lonely", "series_only", cached_at=100.0, file_size=1024
        )

        cache.evict_by_size()
        assert not (tmp_cache_dir / "study_lonely").exists()


class TestDiskWriteConcurrency:
    """Test that _disk_write_semaphore limits concurrent disk writes."""

    @pytest.mark.asyncio
    async def test_disk_write_concurrency_limited(self, tmp_cache_dir: Path) -> None:
        """Concurrent disk writes should be limited by semaphore."""
        cache = DicomWebCache(
            base_dir=tmp_cache_dir,
            disk_write_concurrency=2,
        )

        peak_concurrent = 0
        current_concurrent = 0

        def slow_write(study_uid: str, series_uid: str, instances: dict[str, Any]) -> None:
            """Replacement _write_to_disk that tracks concurrency."""
            nonlocal peak_concurrent, current_concurrent
            # asyncio.Lock can't be used in a thread, use a simple counter
            # Thread-safety: GIL ensures += and -= are atomic for ints
            current_concurrent += 1
            if current_concurrent > peak_concurrent:
                peak_concurrent = current_concurrent
            import time

            time.sleep(0.05)  # simulate I/O
            current_concurrent -= 1

        cache._write_to_disk = slow_write  # type: ignore[assignment]

        instances = _make_instances(1)

        # Launch 5 concurrent background writes
        tasks = [
            asyncio.create_task(cache._write_to_disk_background("study1", f"series_{i}", instances))
            for i in range(5)
        ]
        await asyncio.gather(*tasks)

        # Peak concurrency should not exceed the semaphore limit of 2
        assert peak_concurrent <= 2
        # But we should have actually had concurrent execution
        assert peak_concurrent == 2


class TestStudyLevelCache:
    """Test ensure_study_cached() — study-level C-GET with grouping and locking."""

    @pytest.mark.asyncio
    async def test_all_series_cached_no_cget(self, cache: DicomWebCache) -> None:
        """When all series are already in memory, get_study_to_memory should NOT be called."""
        instances_a = _make_instances(2)
        instances_b = _make_instances(3)
        cache._put_to_memory("study1", "series_a", instances_a)
        cache._put_to_memory("study1", "series_b", instances_b)

        # Mock client and pacs
        mock_client = MagicMock()
        mock_pacs = MagicMock()

        result = await cache.ensure_study_cached(
            study_uid="study1",
            series_uids=["series_a", "series_b"],
            client=mock_client,
            pacs=mock_pacs,
        )

        # Should return both series from memory
        assert len(result) == 2
        assert "series_a" in result
        assert "series_b" in result
        assert result["series_a"].series_uid == "series_a"
        assert result["series_b"].series_uid == "series_b"

        # Client should NOT have been called
        mock_client.get_study_to_memory.assert_not_called()

    @pytest.mark.asyncio
    async def test_partial_cache_fetches_missing(self, cache: DicomWebCache) -> None:
        """Some series in memory, some missing — one study C-GET with correct grouping."""
        # Pre-populate series_a in memory
        instances_a = _make_instances(2)
        cache._put_to_memory("study1", "series_a", instances_a)

        # Mock client to return instances for series_b and series_c
        mock_client = MagicMock()
        mock_pacs = MagicMock()

        # Create mock datasets for series_b and series_c
        ds_b1 = MagicMock()
        ds_b1.SOPInstanceUID = "sop_b1"
        ds_b1.SeriesInstanceUID = "series_b"

        ds_b2 = MagicMock()
        ds_b2.SOPInstanceUID = "sop_b2"
        ds_b2.SeriesInstanceUID = "series_b"

        ds_c1 = MagicMock()
        ds_c1.SOPInstanceUID = "sop_c1"
        ds_c1.SeriesInstanceUID = "series_c"

        # Create mock RetrieveResult
        mock_result = MagicMock()
        mock_result.instances = {
            "sop_b1": ds_b1,
            "sop_b2": ds_b2,
            "sop_c1": ds_c1,
        }
        mock_result.num_completed = 3
        mock_result.status = 0x0000

        # Make get_study_to_memory an AsyncMock
        mock_client.get_study_to_memory = AsyncMock(return_value=mock_result)

        result = await cache.ensure_study_cached(
            study_uid="study1",
            series_uids=["series_a", "series_b", "series_c"],
            client=mock_client,
            pacs=mock_pacs,
        )

        # Should have all 3 series
        assert len(result) == 3
        assert "series_a" in result
        assert "series_b" in result
        assert "series_c" in result

        # series_a should be the original cached entry
        assert result["series_a"].series_uid == "series_a"
        assert len(result["series_a"].instances) == 2

        # series_b should have 2 instances
        assert result["series_b"].series_uid == "series_b"
        assert len(result["series_b"].instances) == 2

        # series_c should have 1 instance
        assert result["series_c"].series_uid == "series_c"
        assert len(result["series_c"].instances) == 1

        # Study C-GET should have been called exactly once
        mock_client.get_study_to_memory.assert_called_once_with(
            study_uid="study1", peer=mock_pacs, on_progress=None
        )

    @pytest.mark.asyncio
    async def test_unexpected_series_cached(self, cache: DicomWebCache) -> None:
        """C-GET returns extra series (SR/KO) not in series_uids — they get cached too."""
        mock_client = MagicMock()
        mock_pacs = MagicMock()

        # Create datasets for requested series_a and unexpected series_sr
        ds_a1 = MagicMock()
        ds_a1.SOPInstanceUID = "sop_a1"
        ds_a1.SeriesInstanceUID = "series_a"

        ds_sr1 = MagicMock()
        ds_sr1.SOPInstanceUID = "sop_sr1"
        ds_sr1.SeriesInstanceUID = "series_sr"

        mock_result = MagicMock()
        mock_result.instances = {
            "sop_a1": ds_a1,
            "sop_sr1": ds_sr1,
        }
        mock_result.num_completed = 2
        mock_result.status = 0x0000

        mock_client.get_study_to_memory = AsyncMock(return_value=mock_result)

        result = await cache.ensure_study_cached(
            study_uid="study1",
            series_uids=["series_a"],  # Only request series_a
            client=mock_client,
            pacs=mock_pacs,
        )

        # Result should only contain requested series_a
        assert len(result) == 1
        assert "series_a" in result
        assert "series_sr" not in result

        # But series_sr should be cached in memory
        cached_sr = cache._get_from_memory("study1", "series_sr")
        assert cached_sr is not None
        assert cached_sr.series_uid == "series_sr"
        assert len(cached_sr.instances) == 1

    @pytest.mark.asyncio
    async def test_empty_cget_raises(self, cache: DicomWebCache) -> None:
        """C-GET returns num_completed=0 — should raise RuntimeError."""
        mock_client = MagicMock()
        mock_pacs = MagicMock()

        # Mock empty result
        mock_result = MagicMock()
        mock_result.instances = {}
        mock_result.num_completed = 0
        mock_result.status = 0xA701  # Failed: Out of Resources

        mock_client.get_study_to_memory = AsyncMock(return_value=mock_result)

        with pytest.raises(RuntimeError, match="Study C-GET returned 0 instances"):
            await cache.ensure_study_cached(
                study_uid="study1",
                series_uids=["series_a"],
                client=mock_client,
                pacs=mock_pacs,
            )

    @pytest.mark.asyncio
    async def test_study_lock_prevents_duplicate(self, cache: DicomWebCache) -> None:
        """Two concurrent calls — only one C-GET should execute."""
        mock_client = MagicMock()
        mock_pacs = MagicMock()

        # Create datasets
        ds_a1 = MagicMock()
        ds_a1.SOPInstanceUID = "sop_a1"
        ds_a1.SeriesInstanceUID = "series_a"

        mock_result = MagicMock()
        mock_result.instances = {"sop_a1": ds_a1}
        mock_result.num_completed = 1
        mock_result.status = 0x0000

        # Create an event to coordinate timing
        first_call_started = asyncio.Event()
        first_call_proceed = asyncio.Event()

        async def mock_get_study(study_uid: str, peer: Any, **_kwargs: Any) -> Any:
            """Mock that allows us to control timing."""
            first_call_started.set()
            await first_call_proceed.wait()
            return mock_result

        mock_client.get_study_to_memory = AsyncMock(side_effect=mock_get_study)

        async def call_ensure_cached() -> dict[str, MemoryCachedSeries]:
            return await cache.ensure_study_cached(
                study_uid="study1",
                series_uids=["series_a"],
                client=mock_client,
                pacs=mock_pacs,
            )

        # Start both calls concurrently
        task1 = asyncio.create_task(call_ensure_cached())
        task2 = asyncio.create_task(call_ensure_cached())

        # Wait for first call to start
        await first_call_started.wait()

        # Give second call a chance to reach the lock
        await asyncio.sleep(0.01)

        # Let the first call complete
        first_call_proceed.set()

        # Both should complete successfully
        result1, result2 = await asyncio.gather(task1, task2)

        # Both should return the same cached series
        assert len(result1) == 1
        assert len(result2) == 1
        assert "series_a" in result1
        assert "series_a" in result2

        # Client should have been called exactly once
        assert mock_client.get_study_to_memory.call_count == 1


class TestStudyUidValidation:
    """Test compensatory guard against study_uid/series_uid pair mismatch.

    Defends OHIF from ``HangingProtocolService`` crashes when a masked
    (anon) StudyInstanceUID is paired with an original SeriesInstanceUID
    — the retrieved DICOM would carry the original StudyInstanceUID and
    break OHIF's displaySet → study linkage.
    """

    def test_validator_passes_on_match(self) -> None:
        ds = MagicMock()
        ds.StudyInstanceUID = "study1"
        DicomWebCache._validate_series_in_study("study1", "series1", {"sop1": ds})

    def test_validator_raises_on_mismatch(self) -> None:
        ds = MagicMock()
        ds.StudyInstanceUID = "real_study"
        with pytest.raises(RuntimeError, match="does not belong to the requested study"):
            DicomWebCache._validate_series_in_study("anon_study", "series1", {"sop1": ds})

    def test_validator_silent_when_attribute_missing(self) -> None:
        # Plain MagicMock auto-creates a non-str attribute — best-effort validator
        # must not raise on fixtures without a real StudyInstanceUID.
        instances = {f"1.2.3.{i}": MagicMock() for i in range(2)}
        DicomWebCache._validate_series_in_study("study1", "series1", instances)

    def test_validator_silent_on_empty_instances(self) -> None:
        DicomWebCache._validate_series_in_study("study1", "series1", {})

    @pytest.mark.asyncio
    async def test_ensure_series_cached_rejects_mismatched_cget(self, cache: DicomWebCache) -> None:
        """C-GET that returns a series under a different StudyInstanceUID must fail loudly."""
        ds = MagicMock()
        ds.SOPInstanceUID = "sop1"
        ds.StudyInstanceUID = "real_study"  # PACS returned original, not anon

        mock_result = MagicMock()
        mock_result.instances = {"sop1": ds}
        mock_result.num_completed = 1
        mock_result.status = 0x0000

        mock_client = MagicMock()
        mock_client.get_series_to_memory = AsyncMock(return_value=mock_result)
        mock_pacs = MagicMock()

        with pytest.raises(RuntimeError, match="does not belong to the requested study"):
            await cache.ensure_series_cached(
                study_uid="anon_study",
                series_uid="series1",
                client=mock_client,
                pacs=mock_pacs,
            )

        # Failed validation must not leave a partial entry in memory
        assert cache._get_from_memory("anon_study", "series1") is None
