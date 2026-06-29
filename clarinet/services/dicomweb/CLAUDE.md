# DICOMweb Proxy Service

Translates DICOMweb HTTP requests (QIDO-RS, WADO-RS) into DICOM C-FIND / C-GET / C-MOVE
operations via `DicomClient`, enabling OHIF Viewer to display images from a traditional
PACS (Orthanc) that only supports DICOM Q/R.

The cache engine itself lives in **`dimsechord`**: a neutral memory+disk `DicomCache`
(SQLite-indexed) and a mem→disk→transport `PullEngine`. This package adds the
Clarinet-specific concerns around them via the `CacheFiller` adapter.

## Architecture

```
dicomweb/
  filler.py     # CacheFiller — adapter over dimsechord DicomCache + PullEngine:
                #   adds the dcm_anon tier-0 + the preload-progress store
  cleanup.py    # DicomWebCacheCleanupService — periodic TTL + size eviction (delegates to the cache)
  service.py    # DicomWebProxyService — QIDO/WADO entry point (DicomClient + CacheFiller)
  __init__.py   # Public API re-exports (MemoryCachedSeries / converters / multipart from dimsechord)
```

Converters (`*_to_dicom_json`), `build_multipart_response` / `extract_frames_from_dataset`,
and `MemoryCachedSeries` are imported from `dimsechord` and re-exported here for callers.

## Cache tiers

```
Request → 1. Memory  (dimsechord DicomCache TTLCache[MemoryCachedSeries], O(1) lookup + LRU)
        ↓ miss
        → 2. dcm_anon ({storage_path}/{patient}/{study}/{series}/dcm_anon/*.dcm — no TTL on the
                       files; the resolved-path cache has one) — CacheFiller tier-0
        ↓ miss
        → 3. Disk  ({storage_path}/dicomweb_cache/{study}/{series}/*.dcm, indexed in index.db)
        ↓ miss
        → 4. PACS  (PullEngine: C-GET, or C-MOVE-to-self via the Storage SCP) → memory,
                   tee'd to the disk tier + SQLite index in the background
```

- **Memory tier** (dimsechord `DicomCache`): `TTLCache` of `MemoryCachedSeries`
  (`dict[sop_uid, Dataset]`). `dicomweb_memory_cache_ttl_minutes` /
  `dicomweb_memory_cache_max_entries` (LRU eviction). `CacheFiller` delegates to
  `DicomCache.{get,put}_series_to_memory`.
- **dcm_anon tier-0** (`CacheFiller`): anonymized `.dcm` written by `AnonymizationService`
  into working-folder `dcm_anon/` subdirs — served *before* the PACS. The files have no TTL.
  Path resolution renders `settings.disk_path_template` against Study/Patient/Series from the
  DB and caches hits **and** misses in a `TTLCache`
  (`dicomweb_dcm_anon_path_cache_ttl_seconds`, default 300). The TTL bounds the negative-cache
  staleness window so an "anonymize-after-first-read" race self-heals; for immediate
  invalidation call `CacheFiller.invalidate_dcm_anon_path(study_uid, series_uid)`.
  **Safe-by-default**: when the anonymized path can't be resolved (`AnonPathError`) the lookup
  reports a miss rather than serving the raw (non-anonymized) UID.
- **Disk tier** (dimsechord `DicomCache`): `.dcm` files under `dicomweb_cache/`, tracked in a
  **SQLite index** (`index.db`) — there are no `.cached_at` filesystem markers; the index holds
  per-instance timestamps and sizes. DICOM on the PACS is immutable, so a present entry is never
  stale. TTL / size eviction (`dicomweb_cache_ttl_hours`, `dicomweb_cache_max_size_gb`) is owned
  exclusively by `DicomWebCacheCleanupService`; `dicomweb_cache_cleanup_enabled=True` is the
  de-facto contract for bounding disk growth.
- **PACS tier** (dimsechord `PullEngine`): on a full miss the engine retrieves from the PACS and
  tees instances to the disk tier on a thread pool (`dicomweb_disk_write_concurrency`),
  recording them in the SQLite index. Per-UID coalescing (duplicate concurrent retrieves) lives
  in the engine. `PullEngine.via_cget(...)` for `c-get*` modes; `PullEngine` + the Storage SCP +
  `AssociationPool` for `c-move*` modes (selected by `settings.dicom_retrieve_mode`). On shutdown
  the filler flushes pending disk writes (`DicomCache.flush_pending_writes`).

### Populating the disk tier from the pipeline

Two ways to warm the disk cache without going through OHIF:

1. **HTTP preload** — `POST /dicom-web/preload` (body `{"study_uids": [...]}`, 1–20 UIDs) via `DicomWebProxyService.start_preload()`. The worker caches studies **sequentially** (one study-level retrieve at a time — don't overload the PACS with associations) and aggregates progress across all of them; poll via `GET /dicom-web/preload/progress/{task_id}`. Fail-fast: an error on study N reports `status="error"` but studies 1..N-1 stay warm, so a retry resumes faster. Progress entries live in a TTLCache (4h) — an expired task_id polls as `not_found`. Fills both memory (API process) and disk tiers. Triggered from the frontend preload widget. Caveat: memory tier holds whole datasets → N concurrent preloads can bloat API server RAM.
2. **Pipeline task** — `prefetch_dicom_web` in `clarinet/services/pipeline/tasks/cache_dicomweb.py`. Runs in a worker process; builds its own dimsechord `DicomCache` + mode-based `PullEngine` per invocation (from `settings` — no `app.state`), matching the API `CacheFiller` layout, then `engine.ensure_series(study, series)` for each missing series — teeing instances to `dicomweb_cache/{study}/{series}/` and the shared SQLite index (`index.db`). Bypasses the memory tier entirely — safe for bulk RecordFlow triggers (`record('x').on_finished().do_task(prefetch_dicom_web)`). Idempotent via `cache.series_cached(...)` (skips already-indexed series) plus the `dcm_anon/` shortcut. Memory tier warms lazily on the next OHIF request.

## Flow

```
OHIF (iframe/tab) → DICOMweb HTTP → FastAPI /dicom-web/ router
  → DicomWebProxyService → CacheFiller (memory / dcm_anon / disk)
    → PullEngine → DicomClient (C-FIND / C-GET / C-MOVE) → Orthanc PACS
```

- **QIDO-RS** (search): query params → `StudyQuery`/`SeriesQuery`/`ImageQuery` → `DicomClient.find_*` (C-FIND) → DICOM JSON
- **WADO-RS series metadata**: `CacheFiller.ensure_series` → `asyncio.to_thread(convert_datasets_to_dicom_json)` (CPU-bound `to_json_dict()` off the event loop) → DICOM JSON with BulkDataURIs
- **WADO-RS frames**: `CacheFiller.ensure_series` → O(1) `instances.get(sop_uid)` → extract pixel data → multipart/related (disk fallback via `CacheFiller.read_instance`, dcm_anon first)
- **WADO-RS study metadata**: C-FIND series → `CacheFiller.ensure_study` — one study-level retrieve instead of N per-series retrieves (the strategy branches on `dicom_retrieve_mode`)

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
| `dicomweb_disk_write_concurrency` | `4` | Max concurrent background disk-write threads |
| `dicomweb_dcm_anon_path_cache_ttl_seconds` | `300` | TTL for the dcm_anon path-resolution cache (bounds the negative-cache window) |
| `ohif_enabled` | `True` | Mount OHIF static files at `/ohif` |

## Disk cache cleanup service

`DicomWebCacheCleanupService` (in `cleanup.py`) runs as a background `asyncio.Task` during app lifespan. It periodically calls, off the event loop via `asyncio.to_thread()`:

1. `filler.evict_expired()` → `DicomCache.evict_expired()` — drops index rows older than `dicomweb_cache_ttl_hours`
2. `filler.evict_by_size()` → `DicomCache.evict_by_size()` — when total size exceeds `dicomweb_cache_max_size_gb`, removes LRU rows first

Both eviction passes are SQLite-index-driven. The service mirrors `SessionCleanupService` and is stored in `app.state.dicomweb_cleanup` (conditional on `dicomweb_cache_cleanup_enabled`).

## Dependencies

- `CacheFiller` is a singleton stored in `app.state.dicomweb_filler` (created in lifespan, shutdown on exit — flushes pending disk writes)
- `DicomWebCacheCleanupService` stored in `app.state.dicomweb_cleanup` (started after the filler, stopped before its shutdown)
- Injected via `get_dicomweb_filler(request: Request)` in `clarinet/api/dependencies.py` → `DicomWebFillerDep`
- `DicomWebProxyServiceDep` gets the singleton filler + a per-request client/pacs

## Key conventions

- Same-origin serving: OHIF at `/ohif`, DICOMweb at `/dicom-web` — cookies work automatically
- Duplicate-retrieve coalescing (per-UID locks) lives in dimsechord's `PullEngine`, not in this package
- `ensure_study` resolves memory → dcm_anon → disk per series first, so an already-anonymized series is served locally and never re-fetched (raw) from the PACS; a locally-resolved series is never overwritten by its raw PACS copy
- `FileNotFoundError` is raised when a requested instance is not in the cache (router handles as 404)
