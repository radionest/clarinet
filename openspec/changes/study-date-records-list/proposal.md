## Why

The shared "Study / Series" cell (Records page and patient detail page) renders only the DICOM `study_description`, and DICOM descriptions repeat — many studies are literally named "abdomen" or "CT Chest" — so the column is unscannable and rows are indistinguishable at a glance. The acquisition date is already in the payload end to end (`Study.date` flows DB → API → frontend decoder), so this is purely a rendering gap. While touching the column, it can also become server-side sortable: the existing sort contract (`SortOrder` → `_SORT_SPECS` → keyset cursor) simply has no study-date entry.

## What Changes

**Study / series cell rendering**
- From: the study part of the cell shows `study_description` alone (`format_study_series_summary`, `clarinet/frontend/src/components/records_list.gleam:399-427`).
- To: the study part leads with the ISO date — `2024-05-12 abdomen` — followed by the unchanged series part and `" / "` join.
- Impact: non-breaking; one shared-widget change fixes both consuming pages (Admin page has the section off). Masking is unchanged: anonymized patients viewed by non-superusers already carry the sentinel `1976-01-01` from the API (`clarinet/api/masking.py:43,154`), displayed verbatim like the Studies section does today.

**Server-side study-date sort**
- From: `POST /records/find` sorts by `id`, `created_at`, `modality_asc/desc`.
- To: plus `study_date_asc` / `study_date_desc` — a new `_SortColumnKind.STUDY_ALIAS` with a per-query `aliased(Study)` outerjoin (mirrors `MODALITY_ALIAS`), `NULLS LAST` in both directions (patient-level records have no study), `Record.id ASC` tie-break, all via the existing generic keyset paths in `find_page` / `_keyset_where`.
- Impact: additive and non-breaking; the keyset cursor payload stays `"v": 1`, so previously issued cursors remain valid. Unknown sort values keep failing with 422.

**Frontend sort mirror**
- From: `SortOrder` ADT ends at `ModalityDesc`; the Study / Series header is static.
- To: `StudyDateAsc` / `StudyDateDesc` constructors, `parse_sort` arm for `"study_date"`, header via `table_sort.th_sortable(..., "study_date", ...)`. The pages' `ColumnHeaderClicked` arms are generic and need no changes.

## Capabilities

### New Capabilities

- `records-list`: date-first rendering of the shared study/series cell and the server-side `study_date` sort contract for `POST /records/find`.

### Modified Capabilities

None — `openspec/specs/` holds no capability covering the records list today.

## Impact

- Backend: `clarinet/utils/pagination.py` (extend the `SortOrder` Literal), `clarinet/repositories/record_repository.py` (`date` import, `STUDY_ALIAS` kind, `_SortSpec.column` signature, two `_SORT_SPECS` entries, `find_page` alias join). The request model (`clarinet/models/record.py`) references the shared Literal and needs no edit.
- Frontend: `clarinet/frontend/src/components/records_list.gleam`, `clarinet/frontend/src/cache/bucket.gleam`, `clarinet/frontend/src/utils/records_query.gleam`.
- Tests: `tests/integration/test_records_find_e2e_sort.py` (fixture + 3 tests), `clarinet/frontend/test/detail_pages_layout_test.gleam`, `clarinet/frontend/test/cache/bucket_test.gleam`, `clarinet/frontend/test/utils/records_query_test.gleam`.
- Docs: `CHANGELOG.md` (Unreleased → Added).
- No DB migration, no new dependency, no new endpoint, no masking changes.

Out of scope: the deployed `nir_ablation` stand runs an older clarinet pin; reaching it requires a downstream pin bump + redeploy.
