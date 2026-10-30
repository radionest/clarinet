# Brainstorm — capability-gated Patients / Studies / Series

Path: architectural (RBAC surface shared by API + frontend).

## Background

- Capability system exists but covers only `reports`: `clarinet/models/capability.py` (`Capability` StrEnum, `resolve_capabilities`, `validate_role_capabilities`), `settings.role_capabilities` (`clarinet/settings.py:313`), `require_capability` factory (`clarinet/api/dependencies.py:497`), frontend `permissions.has_capability` / `router.requires_capability`.
- Patients/studies/series are admin-only in three places: router-wide `Depends(current_admin_user)` (`clarinet/api/routers/study.py:52`), `router.requires_admin_role` (`router.gleam:119`), all-or-nothing nav (`components/layout.gleam:33`).
- Goal: a project can expose these areas to non-admin roles via `settings.role_capabilities`.

## Findings that shaped the design

| # | Finding | Evidence |
|---|---|---|
| F1 | `POST /records/find` is already role-scoped for non-superusers | `record.py:1089` (`role_names`, `include_unassigned`, `exclude_unique_violations`) |
| F2 | Patient + study detail pages load records via that scoped bucket | `bucket.query_with_patient` / `query_with_study` |
| F3 | Series detail renders `SeriesRead.records` — eager-loaded, unscoped, unmasked | `series_repository.py:58`, `pages/series/detail.gleam:170` |
| F4 | Record payloads mask identity for every non-superuser; `PatientRead` carries real `id`/`name` + `anon_id`/`auto_id`/`anon_name` = the de-anonymization mapping | `api/masking.py`, `models/patient.py:64-132` |
| F5 | Repo auto-routes an anon-shaped `patient_id` via `auto_id` for non-superusers; `study_uid` / `series_uid` are matched literally | `record_repository.py:1404`, `:1433`, `:1441` |
| F6 | `SeriesRepository.find_by_anon_uid` exists; no study equivalent | `series_repository.py:87` |
| F7 | Patient detail calls admin-only endpoints (PACS search, activity feed) | `pages/patients/detail.gleam:2,85`, `dicom.py` / `admin.py` `AdminUserDep` |
| F8 | `main.must_redirect` already handles `requires_capability` generically | `main.gleam:1116` |
| F9 | `bucket.RecordsQuery` has no `series_uid`; backend `RecordSearchFilter.series_uid` exists | `cache/bucket.gleam:31-32` |

THEORY: `admin`-role non-superusers today get 404 following a patient link from a masked record (`/patients/CLAR_12` → literal lookup). Confirm/refute: integration test — admin-role non-superuser, anonymized patient, `GET /patients/{anon_id}`.

## Decision chain

| Q | Decision | Source |
|---|---|---|
| Q1 read/write | Split: `patients:read` / `patients:write` (write = create, delete, anonymize, file-events) | user |
| Q2 studies/series | Own capabilities, same split: `studies:read|write`, `series:read|write` | user |
| Q3 records on detail pages | Non-admin sees only records permitted for them (existing role scoping) | user |
| Q4 PHI | Masked view by default; real identifiers only with separate `patients:unmask` | user |
| Q5 per-study anon mode | Patients area needs unmask; a config granting `patients:*` without `patients:unmask` while `anon_per_study_patient_id=True` is a **startup error**, not a runtime 403 | user |
| Q6 un-anonymized rows | Hidden from masked callers (absent in lists, 404 on fetch) | user |
| Q7 approach | A — extend `api/masking.py`; matches existing records pattern (single DTO, `model_copy`, router-applied, anon-id in / real-id never out) | user |
| Q8 write vs read | `X:write` implies `X:read` inside `resolve_capabilities` (no startup error) | user |

Rejected: B — separate masked DTOs (doubles models, Gleam decoders, page code paths); C — serializer-context masking (implicit; forgotten context = leak).

## Validated design

### 1. Capabilities + config validation

| Capability | Grants |
|---|---|
| `patients:read` | `GET /patients`, `GET /patients/{id}` |
| `patients:write` | `POST /patients`, `DELETE /patients/{id}`, `POST …/anonymize`, `POST …/file-events` |
| `patients:unmask` | real identifiers in patient, study **and** series payloads (single unmask switch) |
| `studies:read` | `GET /studies`, `GET /studies/{uid}` |
| `studies:write` | `POST /studies`, `DELETE /studies/{uid}`, `POST …/add_anonymized` |
| `series:read` | `GET /series`, `/series/random`, `/series/{uid}`, `POST /series/find`, `GET /studies/{uid}/series` |
| `series:write` | `POST /series`, `POST /series/{uid}/add_anonymized` |

- `resolve_capabilities`: superuser/`admin` → all; otherwise union over roles, then `X:write` ⇒ add `X:read`.
- `validate_role_capabilities`, per role, on the implied closure, `ConfigurationError` at startup:
  1. any `*:write` ⇒ `patients:unmask` (writers type real ids; write endpoints never resolve anon ids)
  2. `patients:unmask` ⇒ at least one `*:read`
  3. `anon_per_study_patient_id=True` ⇒ any `patients:*` needs `patients:unmask`
- Per-role (not per-user-union) on purpose: a user holding only the offending role would be in the forbidden state.
- `study.py`: drop router-wide admin dep; per-endpoint deps from `require_capability`. Service token resolves to admin → `ClarinetClient` unaffected.

### 2. Masking, addressing, nested data

- Per-request `unmasked = "patients:unmask" in capabilities`.
- New `mask_patient` / `mask_study` / `mask_series` in `api/masking.py` (`model_copy`):

| Model | Masked |
|---|---|
| `PatientRead` | `id`→`anon_id`, `name`→`anon_name` |
| `StudyRead` | `study_uid`→`anon_uid`, `patient_id`→patient `anon_id`, `date`→`1976-01-01` sentinel, `study_description`→`None`, nested `patient` masked |
| `SeriesRead` / `SeriesBase` | `series_uid`→`anon_uid`, `study_uid`→study `anon_uid`, nested `study` masked |

- Anonymized-only filter for masked callers: patients `anon_name IS NOT NULL`; studies `anon_uid IS NOT NULL` + anonymized patient; series `anon_uid IS NOT NULL` + anonymized study. Same filter on nested lists.
- Addressing: masked caller → path id is an anon id **only** (patient via `auto_id` from `{prefix}_N`; study via new `StudyRepository.find_by_anon_uid`; series via existing). Real id from masked caller → 404. Unmasked caller → literal first, anon fallback second.
- Nested data: `PatientRead.studies` emptied without `studies:read`; nested `series` emptied without `series:read`; `SeriesRead.records` emptied for every non-admin; non-empty `records` criteria on `POST /series/find` → 403 for non-admins.
- Records search: for non-superusers `study_uid` / `series_uid` filters match real uid **or** `anon_uid` (parallel to the `patient_id` auto-route).
- Non-goal: record masking itself is unchanged (still keyed on `is_superuser` + `mask_patient_data`).

### 3. Frontend

- `utils/permissions.gleam`: seven capability constants; `is_reports_only` = reports is the *only* capability.
- `router.gleam`: six routes move `requires_admin_role` → `requires_capability` (`Patients`/`PatientDetail`→`patients:read`, `PatientNew`→`patients:write`, `Studies`/`StudyDetail`→`studies:read`, `SeriesDetail`→`series:read`).
- `components/layout.gleam`: flat per-link visibility list (admin-only: Records, Record Types, Workflow, Activity, Admin; capability: Patients, Studies, Reports, Quarto Reports).
- `components/entity_link.gleam`: `*_if_admin(…, is_admin)` → `*_if_allowed(…, allowed)`.
- Write actions behind `:write`: "New Patient", patient Delete + Anonymize, study Delete. "Add Record" stays admin-only.
- Patient detail, non-admin: PACS section + activity feed neither rendered nor initialised; studies section hidden without `studies:read`; series rows plain text without `series:read`.
- Series detail: records from the scoped bucket — `series_uid` on `bucket.RecordsQuery`, `query_with_series`, `key_to_topic`, `cache.bucket_key_to_filter`.

### 4. Tests + docs

- Backend: capability matrix per endpoint (403/200); three startup rules + write⇒read; masked caller (hidden rows, real id 404, anon addressing, masked fields, nested stripping); `SeriesRead.records` empty for non-admin; `/series/find` records criteria 403; records search by anon study/series uid; unmasked anon fallback (also settles the THEORY above). Existing admin tests stay green unchanged.
- Frontend (gleeunit): `requires_capability` per route, `is_reports_only` with mixed capabilities, bucket topic includes series.
- Docs in the same PR: `clarinet/api/CLAUDE.md` auth table, `.claude/rules/api-deps.md`, `.claude/rules/api-urls.md`, `docs/kb/domain-model.md` RBAC section, `settings.py` comment.
- No DB migration.
