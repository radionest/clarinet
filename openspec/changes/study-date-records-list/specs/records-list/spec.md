## ADDED Requirements

### Requirement: Study/series cell leads with the study date

The shared "Study / Series" cell rendered by `format_study_series_summary`
(`clarinet/frontend/src/components/records_list.gleam`) SHALL render the study
part as `<ISO study date> <study_description>` when a description exists, and
as the bare ISO date when it does not. The series part and the `" / "` join
MUST remain unchanged. Records without a study SHALL keep rendering `-`.

#### Scenario: Study with description

- **WHEN** a record has a study with `date = "2024-03-01"` and
  `study_description = "CT Chest"`, and a series with `modality = "MR"` and
  `series_description = "T2 AX"`
- **THEN** the rendered cell contains `2024-03-01 CT Chest / MR - T2 AX`
- **AND** the date is the leading token of the study part

#### Scenario: Study without description

- **WHEN** a record has a study with a date but `study_description = None`
- **THEN** the study part of the cell is the ISO date alone, with no trailing
  space before the series join

#### Scenario: Record without a study

- **WHEN** a record has `study = None` (patient-level record)
- **THEN** the study part of the cell renders `-`
- **AND** no date is shown

### Requirement: Masked rows show the API sentinel verbatim

The UI MUST NOT special-case masked study dates. For anonymized patients
viewed by non-superusers the API replaces `study.date` with the sentinel
`1976-01-01` (`clarinet/api/masking.py`), and the cell SHALL display whatever
date the payload carries.

#### Scenario: Masked row

- **WHEN** a masked record's payload carries `study.date = "1976-01-01"`
- **THEN** the cell renders `1976-01-01` as the leading date token, identical
  to how the Studies section already displays it

### Requirement: Server-side study_date sort

`POST /records/find` SHALL accept `"sort": "study_date_asc"` and
`"sort": "study_date_desc"`, ordering by `Study.date` via a per-query
`aliased(Study)` outerjoin. Null study dates (patient-level records) SHALL
sort last in both directions, and the ordering SHALL tie-break on
`Record.id ASC`. Unknown sort values MUST continue to be rejected with 422.

#### Scenario: Ascending order with nulls last

- **WHEN** the dataset holds three studies with distinct unsorted dates and
  two patient-level records (`study_uid = None`), and the client fetches all
  pages with `"sort": "study_date_asc"`
- **THEN** every non-null date precedes every null date across page
  boundaries
- **AND** the non-null dates appear in temporal ascending order

#### Scenario: Descending order with nulls last

- **WHEN** the same dataset is fetched with `"sort": "study_date_desc"`
- **THEN** every non-null date precedes every null date
- **AND** the non-null dates appear in temporal descending order

#### Scenario: Cursor crosses the null zone

- **WHEN** the client pages the ascending result with `limit = 2` over five
  rows, so page 2 starts at the newest date and page 3 sits fully in the
  null zone
- **THEN** each request returns 200 with a `next_cursor` until the final page
- **AND** no record id appears on two pages

#### Scenario: Unknown sort value

- **WHEN** the request carries `"sort": "study_date"` (no direction suffix)
- **THEN** the response is 422

### Requirement: Cursor compatibility is preserved

The keyset cursor payload version SHALL remain `"v": 1`. Study-date keys
SHALL encode as ISO date strings via the existing JSON-safe fallback and
decode via `date.fromisoformat`. Previously issued cursors for other sort
orders MUST remain valid unchanged.

#### Scenario: Cursor payload shape

- **WHEN** a page is fetched with `"sort": "study_date_asc"` and its
  `next_cursor` is used to fetch the following page
- **THEN** the following page continues after the last record of the previous
  page with no duplicates and no gap
- **AND** the cursor remains decodable by the unmodified `pagination.py`
  version-1 path

### Requirement: Sortable frontend header

The "Study / Series" column header SHALL be rendered sortable with the
backend column key `"study_date"`. The frontend `SortOrder` ADT SHALL gain
`StudyDateAsc` / `StudyDateDesc`; `sort_to_backend_string` SHALL emit
`"study_date_asc"` / `"study_date_desc"` verbatim; `parse_sort` SHALL map
`("study_date", asc|desc)` to those constructors and fall back to `IdAsc` for
unknown keys.

#### Scenario: Header column key

- **WHEN** the records list is rendered with `show_study_series = True`
- **THEN** the Study / Series header is a sortable column keyed
  `"study_date"`

#### Scenario: Sort roundtrip

- **WHEN** `parse_sort` reads `"sort": "study_date"` with `"sort_dir": "desc"`
- **THEN** it yields `StudyDateDesc`
- **AND** `sort_to_backend_string(StudyDateDesc)` equals `"study_date_desc"`

#### Scenario: Unknown header key falls back

- **WHEN** `parse_sort` reads a sort key it does not know
- **THEN** it yields `IdAsc`
