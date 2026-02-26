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

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from cachetools import TTLCache

from src.services.dicomweb.cache import DicomWebCache


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


@pytest.fixture
def cache(tmp_cache_dir: Path) -> DicomWebCache:
    """Create a DicomWebCache with short TTL for testing."""
    return DicomWebCache(
        base_dir=tmp_cache_dir,
        ttl_hours=24,
        max_size_gb=1.0,
        memory_ttl_minutes=30,
        memory_max_entries=50,
    )


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


def _create_disk_series(
    cache_dir: Path, study_uid: str, series_uid: str, cached_at: float, file_size: int = 1024
) -> Path:
    """Create a fake cached series on disk with a .cached_at marker and a dummy file."""
    series_dir = cache_dir / study_uid / series_uid
    series_dir.mkdir(parents=True, exist_ok=True)

    marker = series_dir / ".cached_at"
    marker.write_text(str(cached_at))

    dummy = series_dir / "1.2.3.dcm"
    dummy.write_bytes(b"\x00" * file_size)

    return series_dir


class TestEvictExpired:
    """Test evict_expired() removes expired entries and preserves fresh ones."""

    def test_expired_removed(self, tmp_cache_dir: Path) -> None:
        """Expired series dirs should be removed."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=1)

        # Create an expired entry (2 hours old)
        _create_disk_series(tmp_cache_dir, "study1", "series_old", cached_at=time.time() - 7200)

        removed = cache.evict_expired()
        assert removed == 1
        assert not (tmp_cache_dir / "study1" / "series_old").exists()

    def test_fresh_preserved(self, tmp_cache_dir: Path) -> None:
        """Fresh series dirs should not be removed."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=24)

        _create_disk_series(tmp_cache_dir, "study1", "series_fresh", cached_at=time.time())

        removed = cache.evict_expired()
        assert removed == 0
        assert (tmp_cache_dir / "study1" / "series_fresh").exists()

    def test_empty_study_dirs_cleaned(self, tmp_cache_dir: Path) -> None:
        """After removing all series in a study, the empty study dir should be removed."""
        cache = DicomWebCache(base_dir=tmp_cache_dir, ttl_hours=1)

        _create_disk_series(tmp_cache_dir, "study1", "series_old", cached_at=time.time() - 7200)

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

        _create_disk_series(
            tmp_cache_dir, "study1", "series_oldest", cached_at=100.0, file_size=1024
        )
        _create_disk_series(
            tmp_cache_dir, "study1", "series_middle", cached_at=200.0, file_size=1024
        )
        _create_disk_series(
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

        _create_disk_series(
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

        _create_disk_series(
            tmp_cache_dir, "study_lonely", "series_only", cached_at=100.0, file_size=1024
        )

        cache.evict_by_size()
        assert not (tmp_cache_dir / "study_lonely").exists()
