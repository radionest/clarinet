# DICOMweb Proxy Service

Translates DICOMweb HTTP requests (QIDO-RS, WADO-RS) into DICOM C-FIND/C-GET
operations via the existing `DicomClient`, enabling OHIF Viewer to display images
from a traditional PACS (Orthanc) that only supports DICOM Q/R.

## Architecture

```
dicomweb/
  models.py      # MemoryCachedSeries (in-memory with __slots__)
  converter.py   # DICOM JSON conversion (StudyResult/SeriesResult/ImageResult → tags)
  multipart.py   # WADO-RS multipart/related response builder + frame extraction
  cache.py       # DicomWebCache — four-tier cache (memory + dcm_anon + disk + PACS) with background persistence
  cleanup.py     # DicomWebCacheCleanupService — periodic disk cache TTL + size eviction
  service.py     # DicomWebProxyService — main entry point
  __init__.py    # Public API re-exports
```

## Four-tier cache

```
Request → 1. Memory cache (cachetools.TTLCache[str, MemoryCachedSeries], O(1) lookup + LRU eviction)
        ↓ miss
        → 2. dcm_anon ({storage_path}/{patient}/{study}/{series}/dcm_anon/*.dcm — no TTL)
        ↓ miss
        → 3. Disk cache ({storage_path}/dicomweb_cache/{study}/{series}/*.dcm)
        ↓ miss
        → 4. C-GET to memory (StorageMode.MEMORY) → return immediately
          → background asyncio.Task writes .dcm to dicomweb_cache/
```

- **Memory tier**: `TTLCache` holds `MemoryCachedSeries` with `dict[str, Dataset]` keyed by SOPInstanceUID. TTL controlled by `dicomweb_memory_cache_ttl_minutes`, max entries by `dicomweb_memory_cache_max_entries` (LRU eviction).
- **dcm_anon tier**: Anonymized DICOM files written by `AnonymizationService` into working folder `dcm_anon/` subdirectories. No TTL — files persist until manually deleted. Path lookup iterates patient dirs and caches results in `_dcm_anon_path_cache`.
- **Disk tier**: `.dcm` files + `.cached_at` marker. TTL controlled by `dicomweb_cache_ttl_hours`. Loaded into memory on first access after restart.
- **Background persistence**: After C-GET to memory, `asyncio.create_task` writes datasets to disk via `asyncio.to_thread`, guarded by `_disk_write_semaphore` (default 4) to avoid flooding the thread pool. On shutdown, pending tasks are cancelled.

## Flow

```
OHIF (iframe/tab) → DICOMweb HTTP → FastAPI /dicom-web/ router
  → DicomWebProxyService → DicomWebCache (memory/disk)
    → DicomClient (C-FIND/C-GET) → Orthanc PACS
```

- **QIDO-RS** (search): translates query params → `StudyQuery`/`SeriesQuery`/`ImageQuery` → C-FIND → DICOM JSON response
- **WADO-RS metadata**: ensure_series_cached → `asyncio.to_thread(convert_datasets_to_dicom_json)` (CPU-bound `to_json_dict()` runs off event loop) → DICOM JSON with BulkDataURIs
- **WADO-RS frames**: ensure_series_cached → O(1) `instances.get(sop_uid)` → extract pixel data → multipart/related response
- **WADO-RS study metadata**: C-FIND series → `ensure_study_cached()` — single study-level C-GET instead of N per-series C-GETs

## Settings (`clarinet/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `dicomweb_enabled` | `True` | Mount `/dicom-web` router |
| `dicomweb_cache_ttl_hours` | `24` | Disk cache TTL in hours |
| `dicomweb_cache_max_size_gb` | `10.0` | Max disk cache size in GB |
| `dicomweb_memory_cache_ttl_minutes` | `30` | In-memory cache TTL in minutes |
| `dicomweb_memory_cache_max_entries` | `200` | Max series in memory TTLCache (LRU eviction) |
| `dicomweb_cache_cleanup_enabled` | `True` | Enable periodic disk cache cleanup |
| `dicomweb_cache_cleanup_interval` | `86400` | Cleanup interval in seconds (default: 24h) |
| `dicomweb_disk_write_concurrency` | `4` | Max concurrent background disk write threads |
| `ohif_enabled` | `True` | Mount OHIF static files at `/ohif` |

## Disk cache cleanup service

`DicomWebCacheCleanupService` (in `cleanup.py`) runs as a background `asyncio.Task` during app lifespan. It periodically calls:

1. `cache.evict_expired()` — removes series dirs with `.cached_at` older than TTL
2. `cache.evict_by_size()` — if total disk size exceeds `dicomweb_cache_max_size_gb`, removes oldest entries first

Both methods run off the event loop via `asyncio.to_thread()`. The service follows the same pattern as `SessionCleanupService`. Stored in `app.state.dicomweb_cleanup` (conditional on `dicomweb_cache_cleanup_enabled`).

## Dependencies

- `DicomWebCache` is a singleton stored in `app.state.dicomweb_cache` (created in lifespan, shutdown on exit)
- `DicomWebCacheCleanupService` stored in `app.state.dicomweb_cleanup` (started after cache init, stopped before cache shutdown)
- Injected via `get_dicomweb_cache(request: Request)` in `clarinet/api/dependencies.py`
- `DicomWebProxyServiceDep` gets the singleton cache + per-request client/pacs

## Key conventions

- Same-origin serving: OHIF at `/ohif`, DICOMweb at `/dicom-web` — cookies work automatically
- Cache uses `asyncio.Lock` per (study_uid, series_uid) to prevent duplicate C-GETs; study-level lock (`{study_uid}/__STUDY__`) prevents duplicate study C-GETs
- `ensure_study_cached()` retrieves all missing series in one study-level C-GET, groups by SeriesInstanceUID, caches unexpected series (SR/KO/PR) too
- `FileNotFoundError` raised when cached instance not found (router handles as 404)
- `StorageHandler.stored_instances` is `dict[str, Dataset]` keyed by SOPInstanceUID (not a list)
