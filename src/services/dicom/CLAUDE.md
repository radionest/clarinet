# DICOM Service

Async DICOM client for Query/Retrieve operations against external PACS servers (e.g. Orthanc).

## Architecture

```
dicom/
  models.py         # Pydantic models: DicomNode, queries, results, storage config
  operations.py     # Synchronous pynetdicom wrapper (C-FIND, C-GET, C-MOVE)
  handlers.py       # C-STORE event handlers (disk / memory / forward modes)
  client.py         # Async facade — delegates to operations via asyncio.to_thread()
  anonymizer.py     # Anonymizer, PACS stubs (planned; not yet exported)
  series_filter.py  # Configurable series filter (modality blocklist, instance count, unknown policy)
  __init__.py       # Public API re-exports
```

- `DicomClient` is the main entry point — all methods are async
- `DicomOperations` is synchronous; never call it directly from async code
- `StorageHandler` handles incoming C-STORE events in three modes: `DISK`, `MEMORY`, `FORWARD`

## Settings (`src/settings.py`)

| Setting | Default | Description |
|---|---|---|
| `dicom_aet` | `CLARINET` | Local AE title |
| `dicom_port` | `11112` | Local DICOM port |
| `dicom_ip` | `None` | Local DICOM IP |
| `dicom_max_pdu` | `16384` | Maximum PDU size |
| `pacs_aet` | `ORTHANC` | Remote PACS AE title |
| `pacs_host` | `localhost` | Remote PACS host |
| `pacs_port` | `4242` | Remote PACS port |

Env vars use `CLARINET_` prefix (e.g. `CLARINET_PACS_HOST`).

## Test PACS (Orthanc on klara)

- Host: `192.168.122.151`
- DICOM port: `4242`, AET: `ORTHANC`
- REST API: `http://192.168.122.151:8042` (no auth)
- All operations allowed: C-ECHO, C-FIND, C-GET, C-MOVE, C-STORE

## Usage

```python
from src.services.dicom import (
    DicomClient, DicomNode, StudyQuery, SeriesQuery,
    PacsImportRequest, PacsStudyWithSeries, RetrieveResult,
    StorageMode,
)
from src.settings import settings

client = DicomClient(calling_aet=settings.dicom_aet, max_pdu=settings.dicom_max_pdu)
pacs = DicomNode(aet=settings.pacs_aet, host=settings.pacs_host, port=settings.pacs_port)

studies = await client.find_studies(query=StudyQuery(patient_id="12345"), peer=pacs)
result = await client.get_study(study_uid=studies[0].study_instance_uid, peer=pacs, output_dir=Path("./out"))
```

## Series Filter

`SeriesFilter` excludes non-image series (SR, KO, PR, etc.) at import and/or anonymization time.
- Pure logic, no I/O — operates on `SeriesFilterCriteria` DTO
- `SeriesFilterCriteria.from_series_result()` for import time (PACS C-FIND data)
- `SeriesFilterCriteria.from_series()` for anonymization time (DB model)
- Settings: `series_filter_excluded_modalities`, `series_filter_min_instance_count`, `series_filter_unknown_modality_policy`, `series_filter_on_import`

## Batch C-STORE

`store_instances_batch` sends multiple datasets over a single DICOM association (vs `store_instance` which opens one association per dataset).

- **`operations.py`**: `store_instances_batch(config, datasets)` → `BatchStoreResult` (sync, one `ae.associate()`, loops `send_c_store`)
- **`client.py`**: `store_instances_batch(datasets, peer)` → async wrapper via `asyncio.to_thread()`
- **`models.py`**: `BatchStoreResult(total_sent, total_failed, failed_sop_uids)`
- Used by `AnonymizationService._send_series_to_pacs()` for per-series batch distribution

## Key conventions

- All I/O goes through `asyncio.to_thread()` because pynetdicom is synchronous
- Exceptions: `CONFLICT` for association failures, `NOT_FOUND` where applicable
- Logger: `from src.utils.logger import logger`
