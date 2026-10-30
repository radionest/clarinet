## Context

No new data is needed: `Study.date: date` is a required DB column
(`clarinet/models/study.py:23`), `StudyRead` inherits it, so `POST
/records/find` and `GET /records/{id}` already return `study.date`, and the
frontend already decodes it (`clarinet/frontend/src/api/records.gleam:194` →
`models.Study.date: String`, `clarinet/frontend/src/api/models.gleam:25`).

The cell is rendered by `format_study_series_summary`
(`clarinet/frontend/src/components/records_list.gleam:399-427`), which uses
only `study.study_description`. The widget is shared by the Records page and
the patient detail page (`show_study_series: True` in
`pages/records/list.gleam:243` and `pages/patients/detail.gleam:548`); the
Admin page has it off (`pages/admin.gleam:719`). One change fixes both pages.

Masking: for anonymized patients viewed by non-superusers (record types with
`mask_patient_data=True`), the API already replaces `study.date` with
sentinel `1976-01-01` (`clarinet/api/masking.py:43`, `:154`) and the UI
displays it verbatim — same as the Studies section via
`entity_link.gleam:49`.

Sort machinery today: `SortOrder` Literal (`clarinet/utils/pagination.py:12-26`),
`_SORT_SPECS` + `_SortColumnKind` in
`clarinet/repositories/record_repository.py` (`MODALITY_ALIAS` kind does a
per-query `aliased(Series)` outerjoin in `find_page`), generic keyset paths
`_keyset_where` (nullable branch `:295-305`) and `encode_cursor` via
`_SORT_SPECS[sort].extract_key`. Frontend mirror: `SortOrder` ADT +
`sort_to_backend_string` in `cache/bucket.gleam`, `parse_sort` in
`utils/records_query.gleam`, `table_sort.th_sortable` for headers.

Constraints:

- Keyset cursor payload version stays `"v": 1` (`clarinet/utils/pagination.py:59`)
  — the new sort orders are additive; previously issued cursors remain valid.
- Tests must stay PostgreSQL + SQLite portable (patterns in
  `tests/integration/test_records_find_e2e_sort.py`: no UUID-ordering
  assertions; ISO date strings sort identically in both engines).
- Do not special-case the masked sentinel anywhere in the UI.
- The deployed `nir_ablation` stand runs an older clarinet pin (it renders
  the removed `^` join); reaching it requires a pin bump + redeploy
  downstream — out of scope.

## Goals / Non-Goals

**Goals:**

- The study/series cell is scannable when descriptions repeat: the date leads
  at a fixed width.
- The column sorts server-side with keyset-cursor safety, including the
  patient-level NULL zone.
- Both consuming pages get the behaviour from the one shared widget; page
  update arms stay generic.

**Non-Goals:**

- No new column, no locale date formatting (`format_dicom_date` at
  `pages/patients/detail.gleam:709` is for DICOM `YYYYMMDD` strings from PACS
  search — not applicable).
- No masking changes of any kind.
- No stand rollout (downstream pin bump).

## Decisions

Registered in `openspec/decisions.md` as D16–D20 (coined 2026-10-29).

### D16: Date-first ISO rendering of the study/series cell

- **Choice**: The study part renders as `2024-05-12 abdomen` — the ISO date
  leads at a fixed width so same-named studies ("abdomen") are told apart by
  scanning the leading dates down the column. Series part and the `" / "`
  join are unchanged; a record without a study keeps `-`.
- **Rationale**: `study.date` arrives ISO from Pydantic, so no formatting is
  needed; a leading fixed-width token is what makes the column scannable.
- **Alternative**: a separate date column — rejected: the table already has
  many columns and the shared cell is consumed by two pages.

### D17: `study_date` sort key with a STUDY_ALIAS join

- **Choice**: New `SortOrder` values `study_date_asc` / `study_date_desc`; new
  `_SortColumnKind.STUDY_ALIAS` with a per-query `aliased(Study)` outerjoin in
  `find_page` (mirrors `MODALITY_ALIAS`). `nullable=True` → NULLS LAST in both
  directions (patient-level records have no study), tie-break `Record.id ASC`
  — both already provided by the generic paths in `find_page` /
  `_keyset_where`. `extract_key` reads `r.study.date if r.study else None`.
- **Rationale**: reuses the proven alias-sort pattern instead of inventing a
  third mechanism; the generic nullable keyset branch already handles the
  NULL zone and `cast_cursor`.
- **Caveat**: same eager-load caveat as `MODALITY_ALIAS` — `extract_key`
  reads `r.study.date` from the relationship, so it depends on
  `selectinload(Record.study)` staying in `_build_criteria_query`; removing it
  would cause a `MissingGreenlet` in async context. Documented on the kind.

### D18: Keyset cursor stays v1

- **Choice**: The cursor payload version stays `"v": 1`. `date` values pass
  through the existing `_to_json_safe` fallback (`str(value)` →
  `"2025-06-01"`, `pagination.py:46`); decode via
  `cast_cursor=lambda raw: date.fromisoformat(raw)`.
- **Rationale**: additive sort orders must not invalidate previously issued
  cursors; ISO strings round-trip losslessly for a `date` column.

### D19: Frontend sort mirror

- **Choice**: ADT constructors `StudyDateAsc` / `StudyDateDesc` in
  `cache/bucket.gleam` (distinct cache-bucket keys come free via
  `key_to_topic`); header column key `"study_date"`. The pages'
  `ColumnHeaderClicked` arms are generic (`table_sort.next_sort` /
  `write_sort` — `pages/records/list.gleam:141`,
  `pages/patients/detail.gleam:297`) and need no changes.
- **Rationale**: `sort_to_backend_string` must emit exactly the backend
  strings, so the contract is pinned by tests on both sides
  (`sort_to_backend_string` test, `parse_sort` tests, pytest 422-before/200-after).

### D20: Masked rows show the 1976-01-01 sentinel

- **Choice**: Masked rows display `1976-01-01` as the leading date token; no
  UI special-casing anywhere.
- **Rationale**: it is the sanctioned masked value already shown by the
  Studies section (`entity_link.gleam:49`); a UI-side hide would leak that a
  date was suppressed.

## Risks / Trade-offs

- [Risk] `extract_key` hits `MissingGreenlet` if `selectinload(Record.study)`
  is removed from `_build_criteria_query` → Mitigation: the caveat is
  documented on `_SortColumnKind.STUDY_ALIAS` next to the existing
  `MODALITY_ALIAS` one, and the new integration tests fail loudly if the load
  disappears.
- [Risk] Sort-order tests break engine portability → Mitigation: assert on
  ISO date strings only (they sort temporally in both PostgreSQL and SQLite);
  no UUID-ordering assertions.
- [Trade-off] A masked row reads as a real 1976 date → accepted (D20); it is
  the value the rest of the UI already shows.
- [Trade-off] The date arrives in every payload already, so Phase 1 ships no
  API change → intentional; the change is rendering + sorting only.

## Migration Plan

- No DB migration; additive Literal + spec entries only.
- Deploy is inert until a client sends the new sort values or a user sorts the
  column. Old cursors stay valid (D18).
- Rollback: revert the release; previously issued study-date cursors die with
  the reverted Literal (422 on use), which is acceptable for a UI-driven sort.

## Open Questions

None.
