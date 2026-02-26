# DICOMweb Proxy Service

Translates DICOMweb HTTP requests (QIDO-RS, WADO-RS) into DICOM C-FIND/C-GET
operations via the existing `DicomClient`, enabling OHIF Viewer to display images
from a traditional PACS (Orthanc) that only supports DICOM Q/R.

## Architecture

```
dicomweb/
  models.py      # CachedSeries (disk), MemoryCachedSeries (in-memory with __slots__)
  converter.py   # DICOM JSON conversion (StudyResult/SeriesResult/ImageResult → tags)
  multipart.py   # WADO-RS multipart/related response builder + frame extraction
  cache.py       # DicomWebCache — two-tier cache (memory + disk) with background persistence
  cleanup.py     # DicomWebCacheCleanupService — periodic disk cache TTL + size eviction
  service.py     # DicomWebProxyService — main entry point
  __init__.py    # Public API re-exports
```

## Two-tier cache

```
Request → Memory cache (cachetools.TTLCache[str, MemoryCachedSeries], O(1) lookup + LRU eviction)
        ↓ miss
        → Disk cache ({storage_path}/dicomweb_cache/{study}/{series}/*.dcm)
        ↓ miss
        → C-GET to memory (StorageMode.MEMORY) → return immediately
          → background asyncio.Task writes .dcm to disk
```

- **Memory tier**: `TTLCache` holds `MemoryCachedSeries` with `dict[str, Dataset]` keyed by SOPInstanceUID. TTL controlled by `dicomweb_memory_cache_ttl_minutes`, max entries by `dicomweb_memory_cache_max_entries` (LRU eviction).
- **Disk tier**: `.dcm` files + `.cached_at` marker. TTL controlled by `dicomweb_cache_ttl_hours`. Loaded into memory on first access after restart.
- **Background persistence**: After C-GET to memory, `asyncio.create_task` writes datasets to disk via `asyncio.to_thread`. On shutdown, pending tasks are cancelled.

## Flow

```
OHIF (iframe/tab) → DICOMweb HTTP → FastAPI /dicom-web/ router
  → DicomWebProxyService → DicomWebCache (memory/disk)
    → DicomClient (C-FIND/C-GET) → Orthanc PACS
```

- **QIDO-RS** (search): translates query params → `StudyQuery`/`SeriesQuery`/`ImageQuery` → C-FIND → DICOM JSON response
- **WADO-RS metadata**: ensure_series_cached → iterate `MemoryCachedSeries.instances.values()` → strip PixelData from copy → DICOM JSON with BulkDataURIs
- **WADO-RS frames**: ensure_series_cached → O(1) `instances.get(sop_uid)` → extract pixel data → multipart/related response
- **WADO-RS study metadata**: C-FIND series → `asyncio.gather()` parallel metadata retrieval per series

## Settings (`src/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `dicomweb_enabled` | `True` | Mount `/dicom-web` router |
| `dicomweb_cache_ttl_hours` | `24` | Disk cache TTL in hours |
| `dicomweb_cache_max_size_gb` | `10.0` | Max disk cache size in GB |
| `dicomweb_memory_cache_ttl_minutes` | `30` | In-memory cache TTL in minutes |
| `dicomweb_memory_cache_max_entries` | `50` | Max series in memory TTLCache (LRU eviction) |
| `dicomweb_cache_cleanup_enabled` | `True` | Enable periodic disk cache cleanup |
| `dicomweb_cache_cleanup_interval` | `86400` | Cleanup interval in seconds (default: 24h) |
| `ohif_enabled` | `True` | Mount OHIF static files at `/ohif` |

## Disk cache cleanup service

`DicomWebCacheCleanupService` (in `cleanup.py`) runs as a background `asyncio.Task` during app lifespan. It periodically calls:

1. `cache.evict_expired()` — removes series dirs with `.cached_at` older than TTL
2. `cache.evict_by_size()` — if total disk size exceeds `dicomweb_cache_max_size_gb`, removes oldest entries first

Both methods run off the event loop via `asyncio.to_thread()`. The service follows the same pattern as `SessionCleanupService`. Stored in `app.state.dicomweb_cleanup` (conditional on `dicomweb_cache_cleanup_enabled`).

## Dependencies

- `DicomWebCache` is a singleton stored in `app.state.dicomweb_cache` (created in lifespan, shutdown on exit)
- `DicomWebCacheCleanupService` stored in `app.state.dicomweb_cleanup` (started after cache init, stopped before cache shutdown)
- Injected via `get_dicomweb_cache(request: Request)` in `src/api/dependencies.py`
- `DicomWebProxyServiceDep` gets the singleton cache + per-request client/pacs

## Key conventions

- Same-origin serving: OHIF at `/ohif`, DICOMweb at `/dicom-web` — cookies work automatically
- Cache uses `asyncio.Lock` per (study_uid, series_uid) to prevent duplicate C-GETs
- `FileNotFoundError` raised when cached instance not found (router handles as 404)
- `StorageHandler.stored_instances` is `dict[str, Dataset]` keyed by SOPInstanceUID (not a list)
