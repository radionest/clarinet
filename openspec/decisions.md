# Decision register

This file owns every globally-cited decision namespace for the project: `D<n>`
for study and pipeline design decisions, `F<n>` for the clarinet framework-API
mapping. It is a sibling of `openspec/specs/`, belongs to no change, and is
never moved or archived — a `→ D<n>` or `→ F<n>` pointer from source, tests or
documentation resolves here permanently.

**This file answers "why", and nothing else.** An entry states a choice this
project made and the date it was settled — not the evidence that supported it,
and not what else was tried.
How a dependency *behaves* is not a decision and belongs in the code, or in the
path-scoped rule that loads where it is acted on.

A change drafts its decisions in its own `design.md`, where they are argued and
revised, and writes each one here when the number is coined. A superseded entry
is revised in place with its revision date, never deleted. An entry whose
content turns out to be mechanism is **retired** to a one-line stub naming the
date and where the content went, so every pointer still resolves.

A number is never reused. Numbers are unique within a namespace, not across
them. `D<n>` entries sit under `###` sections and ascend within one, and the
index maps every one of them to its section; `## Framework mapping` holds
retired `F<n>` stubs only.

Archiving a change MUST, after the delta-spec sync and before moving the change
directory, append every decision the change coined to this register and leave a
one-line pointer in its `design.md`. Same stop-on-failure semantics as the spec
sync: on failure nothing moves.

Before coining a decision number, check it against this register AND every
other change's `design.md`. Numbers are unique within a namespace across all
changes, active and archived.

## Decisions

### Index

| # | Section | Decision |
|---|---|---|
| D15 | [Admin CLI password reset](#d15-admin-cli-password-reset-targets-any-existing-user) | `admin reset-password` targets any existing user; the `is_superuser` guard was a false restriction |
| D16 | [Date-first ISO rendering of the studyseries cell](#d16-date-first-iso-rendering-of-the-studyseries-cell) | the shared study/series cell leads with the ISO study date, no separate column, no locale formatting |
| D17 | [study_date sort key with a STUDY_ALIAS join](#d17-study_date-sort-key-with-a-study_alias-join) | `study_date_asc`/`study_date_desc` sort via a per-query `aliased(Study)` outerjoin, NULLS LAST, id tie-break |
| D18 | [Keyset cursor stays v1](#d18-keyset-cursor-stays-v1) | study-date cursors ride the unmodified v1 payload; dates encode as ISO strings |
| D19 | [Frontend sort mirror](#d19-frontend-sort-mirror) | `StudyDateAsc`/`StudyDateDesc` ADT + header key `"study_date"`; page update arms stay generic |
| D20 | [Masked rows show the 1976-01-01 sentinel](#d20-masked-rows-show-the-19760101-sentinel) | masked study dates display the API sentinel verbatim; the UI never special-cases it |

## Decision entries

### D15: Admin CLI password reset targets any existing user

Settled 2026-10-02 (change: `admin-reset-any-user`). The CLI password-reset
helper matches by email and resets regardless of `is_superuser`; renamed
`reset_admin_password` → `reset_user_password`. The dropped guard read as
security but protected nothing — CLI access already implies shell access to
the server and its database. Sessions are revoked on reset (inherited from
`update_password`'s #651 semantics; the pre-#651 "no revocation" stance
died with the API path's own change).

### D16: Date-first ISO rendering of the study/series cell

Settled 2026-10-29 (change: `study-date-records-list`). The shared
study/series cell renders the study part as `<ISO study date>
<study_description>` (`2024-05-12 abdomen`), with the series part and the
`" / "` join unchanged; records without a study keep `-`. No separate
column and no locale formatting — the ISO date arrives from Pydantic as-is,
and the fixed-width leading token is what makes repeated descriptions
scannable down the column.

### D17: study_date sort key with a STUDY_ALIAS join

Settled 2026-10-29 (change: `study-date-records-list`). `POST /records/find`
gains `study_date_asc` / `study_date_desc`, resolved through a new
`_SortColumnKind.STUDY_ALIAS` and a per-query `aliased(Study)` outerjoin in
`find_page`, mirroring the existing `MODALITY_ALIAS` pattern. Null study
dates sort last in both directions and the order tie-breaks on
`Record.id ASC` — both provided by the generic keyset paths. Like
`MODALITY_ALIAS`, `extract_key` reads the eager-loaded relationship, so
`selectinload(Record.study)` must stay in `_build_criteria_query`.

### D18: Keyset cursor stays v1

Settled 2026-10-29 (change: `study-date-records-list`). Study-date cursors
use the unmodified version-1 payload: the `date` encodes as an ISO string
through the existing `_to_json_safe` fallback and decodes via
`date.fromisoformat` in the spec's `cast_cursor`. Additive sort orders must
not invalidate previously issued cursors.

### D19: Frontend sort mirror

Settled 2026-10-29 (change: `study-date-records-list`). The frontend
`SortOrder` ADT gains `StudyDateAsc` / `StudyDateDesc`, the header column
key is `"study_date"`, and `sort_to_backend_string` emits the backend
strings verbatim. The consuming pages' `ColumnHeaderClicked` arms are
generic (`table_sort.next_sort` / `write_sort`) and take no page changes;
distinct constructors keep distinct cache-bucket keys via `key_to_topic`.

### D20: Masked rows show the 1976-01-01 sentinel

Settled 2026-10-29 (change: `study-date-records-list`). Rows masked by
`mask_patient_data` display the API's `1976-01-01` sentinel as the leading
date token, exactly as the Studies section already does. The UI never
special-cases the sentinel: it renders whatever date the payload carries.

## Framework mapping

(retired `F<n>` stubs only)
