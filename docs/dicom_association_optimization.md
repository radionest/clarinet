# DICOM Association Optimization

## Problem

When opening a study in OHIF Viewer (`GET /dicom-web/studies/{uid}/metadata`),
the DICOMweb proxy discovers series via C-FIND, then retrieves metadata for each
series. Previously, this launched **N parallel C-GET operations** — one DICOM
association per series. For studies with 40+ series, this created ~90+
simultaneous associations against Orthanc PACS, causing:

- ACSE timeout errors (association establishment failures)
- OHIF Viewer hangs waiting for metadata
- PACS server resource exhaustion

## Solution

Two complementary changes:

### 1. Study-level C-GET (`ensure_study_cached()`)

Instead of N per-series C-GET associations, a single study-level C-GET retrieves
all instances in one association. The results are grouped by `SeriesInstanceUID`
and cached per-series in memory.

**Flow:**
```
retrieve_study_metadata()
  → C-FIND series UIDs
  → ensure_study_cached(study_uid, series_uids)
    → check memory/dcm_anon/disk for each series
    → collect missing series
    → ONE study-level C-GET for all missing series
    → group instances by SeriesInstanceUID
    → cache each group in memory + background disk write
```

**Key behaviors:**
- Series already in cache (any tier) are not re-fetched
- Study-level `asyncio.Lock` prevents duplicate C-GETs for the same study
- Unexpected series from C-GET (SR, KO, PR) are also cached for future use
- `retrieve_series_metadata()` and `retrieve_frames()` still use per-series
  `ensure_series_cached()` for single-series requests

### 2. Global Association Semaphore

A `threading.Semaphore` in `DicomOperations._association()` limits the total
number of concurrent DICOM associations across all operations (DICOMweb proxy,
anonymization, import, etc.).

**Why `threading.Semaphore`:**
- `_association()` is synchronous, called via `asyncio.to_thread()`
- `asyncio.Semaphore` is not thread-safe
- Class-level (not instance-level) because `DicomClient` is created per-request

## Configuration

| Setting | Default | Env Variable | Description |
|---|---|---|---|
| `dicom_max_concurrent_associations` | `8` | `CLARINET_DICOM_MAX_CONCURRENT_ASSOCIATIONS` | Max simultaneous DICOM associations |
| `dicomweb_memory_cache_max_entries` | `200` | `CLARINET_DICOMWEB_MEMORY_CACHE_MAX_ENTRIES` | Max series in memory cache (was 50) |

The memory cache default was increased from 50 to 200 because a single study with
42 series occupies 42 slots — the old limit caused rapid cache eviction.

## Monitoring

### Healthy study load (single C-GET)
```
INFO  Cache miss for 42 series — retrieving study 1.2.3... via single C-GET
INFO  Study C-GET completed: 1247 instances across 42 series
INFO  WADO-RS study metadata: 1247 instances across 42 series for study 1.2.3...
```

### Cache hit (no PACS call)
```
DEBUG All 42 series for study 1.2.3... found in cache
```

### PACS overload symptoms (pre-fix)
```
ERROR Failed to establish association with ORTHANC
```
Multiple such errors in quick succession indicate the semaphore limit may need
to be increased, or the PACS server needs more resources.

## Tuning

- **`dicom_max_concurrent_associations=8`**: Good default for Orthanc on modest
  hardware. Increase to 16-32 for dedicated PACS servers with high throughput.
  Decrease to 4 if PACS is shared or resource-constrained.
- **`dicomweb_memory_cache_max_entries=200`**: Supports ~4-5 large studies
  simultaneously. Increase for multi-user environments with many concurrent viewers.
