# Capability-Gated Patients / Studies / Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let projects expose the Patients / Studies / Series areas to non-admin roles through `settings.role_capabilities`, with identity masking unless the role holds `patients:unmask`.

**Architecture:** Seven new members in the existing `Capability` enum; per-endpoint `require_capability` guards replace the router-wide admin dependency on `study.py`; a per-request `EntityAccess` record drives repository visibility (anonymized-only, anon addressing) and router-applied `mask_*` functions beside `mask_records`. Frontend moves six routes to capability guards and the nav to per-link visibility.

**Tech Stack:** FastAPI, SQLModel/SQLAlchemy async, pytest + pytest-asyncio, Gleam + Lustre, gleeunit.

**Spec:** `openspec/changes/capability-gated-patients-studies-series/` — `design.md` (D1-D9), `specs/entity-access-capabilities/spec.md`, `specs/entity-identity-masking/spec.md`, `specs/frontend-capability-gating/spec.md`. Every scenario there is a test case; task test lists below name them.

## Global Constraints

- Work in a worktree (`EnterWorktree`); link the spec store first: `ln -s "$MAIN/openspec" openspec` (see `~/.claude/rules/openspec-artifacts.md`). Never `git add` anything under `openspec/`.
- Python via `uv` only. Tests: `./scripts/run_tests.sh <paths> -q > /tmp/test-<worktree>.txt 2>&1`, wrapped in `timeout 120` (`timeout 300` for the first run in a fresh worktree). Never pipe. Do not re-run a test without an `Edit` in between.
- After `make check`, `Read` a file again before the next `Edit` (ruff may rewrite it).
- DB access only in `clarinet/repositories/`; repositories raise `clarinet.exceptions.domain` only; routers use `clarinet.exceptions.http` (`FORBIDDEN` at `http.py:37`).
- No `asyncio.gather` on the shared `AsyncSession`.
- Typing: no bare `dict`/`list`/`object`; fixed string sets are `Literal`/`StrEnum`; records are `@dataclass(frozen=True, slots=True)`.
- Test URLs come from `tests/utils/urls.py` — no hardcoded strings. No new URLs are added by this change.
- Non-admin test clients: `create_mock_user_with_role(test_session, "<role>", email=...)` + `monkeypatch.setattr(settings, "role_capabilities", {...})` + `create_authenticated_client(...)` — pattern at `tests/integration/test_reports.py:143-155`.
- Patients in tests: `make_patient` / `PatientFactory` only (never `Patient(...)` directly — `auto_id` is NOT NULL).
- Commits: conventional messages, no Co-Authored-By trailer (user global rule).
- Admin behaviour must not change: baseline selectors below stay green **unedited**.

**Baseline gates (validated 2026-09-20 with `--collect-only -q`):**

| Selector | Expected |
|---|---|
| `tests/test_capabilities.py tests/test_require_capability.py tests/test_bootstrap_capabilities.py` | 11 collected before Task 1; 24 after |
| `tests/integration/test_study_crud.py tests/test_client.py` | 62 collected, all pass, files unedited |

New test files pin their count as the number of test names listed in their task. After writing a file, run `--collect-only -q` on it and confirm the count matches before treating it as a gate.

---

### Task 1: Capability vocabulary, write-implies-read, startup rules

**Files:**
- Modify: `clarinet/models/capability.py:17-56`, `clarinet/settings.py:309-313` (comment only)
- Test: `tests/test_capabilities.py`, `tests/test_bootstrap_capabilities.py`

**Interfaces:**
- Produces: `Capability.PATIENTS_READ = "patients:read"`, `PATIENTS_WRITE`, `PATIENTS_UNMASK = "patients:unmask"`, `STUDIES_READ`, `STUDIES_WRITE`, `SERIES_READ`, `SERIES_WRITE`. `resolve_capabilities(role_names, is_superuser) -> list[str]` and `validate_role_capabilities(mapping: dict[str, list[str]]) -> None` keep their signatures.
- Internal: `_WRITE_IMPLIES_READ: dict[Capability, Capability]`; `_with_implied(caps: Iterable[str]) -> set[str]`.

- [ ] **Step 1: Failing tests** in `tests/test_capabilities.py` (12 new; file goes 7 → 19):
  `test_vocabulary_has_entity_capabilities`; `test_write_implies_read` (role → `["studies:write","patients:unmask"]` resolves to read+write+unmask); `test_validate_rejects_masked_writer` (`["patients:read","patients:write"]`); `test_validate_rejects_unmask_alone`; `test_validate_accepts_unmask_with_write_only` (`["patients:unmask","series:write"]`); `test_validate_per_study_rejects_masked_patient_reader`; `test_validate_per_study_accepts_masked_study_reader` (`["studies:read","series:read"]`); `test_validate_per_patient_accepts_masked_patient_reader`; `test_validate_error_names_role` (message contains the role name); `test_validate_checks_each_role` (second role offends); `test_resolve_unions_roles_then_implies`; `test_admin_role_gets_entity_capabilities`.
  Per-study tests use `monkeypatch.setattr(settings, "anon_per_study_patient_id", True)`.
- [ ] **Step 2:** one bootstrap test in `tests/test_bootstrap_capabilities.py` (1 → 2): `test_rejects_masked_writer_before_db` — mirrors `test_rejects_unknown_capability` with `{"coord": ["patients:write"]}`.
- [ ] **Step 3:** run the baseline selector → 13 new tests fail, 11 pass.
- [ ] **Step 4: Implement.** Add members. `_with_implied` adds `X:read` for each `X:write`. `resolve_capabilities`: admin/superuser unchanged; else `sorted(_with_implied(granted))`. `validate_role_capabilities`: unknown-name check first (unchanged), then per role on `_with_implied(caps)`: any `*:write` without `patients:unmask` → error; `patients:unmask` without any `*:read` → error; `settings.anon_per_study_patient_id` and any `patients:*` without `patients:unmask` → error. One `ConfigurationError` per offending role, message names the role and the rule.
- [ ] **Step 5:** update the `role_capabilities` comment in `settings.py` — list the vocabulary source file and the three rules in one line each.
- [ ] **Step 6:** run the baseline selector → 24 pass. Commit `feat(capabilities): entity capabilities with startup validation`.

---

### Task 2: Repository visibility and anon addressing

**Files:**
- Modify: `clarinet/models/patient.py` (beside `anon_id`, `:81`), `clarinet/repositories/patient_repository.py:113-155`, `clarinet/repositories/study_repository.py:26-71`, `clarinet/repositories/series_repository.py:87,250-300`
- Create test: `tests/test_entity_visibility_repos.py`

**Interfaces — Produces:**
- `clarinet.models.patient.auto_id_from_anon_id(value: str) -> int | None` — strict: `value` must start with `f"{settings.anon_id_prefix}_"` and the rest be `int`; else `None`. Do **not** touch the two existing inline parses in `record_repository.py:1404,1422` (they differ subtly; out of scope).
- `PatientRepository.get_all_with_studies(skip=0, limit=100, *, anonymized_only: bool = False)`; `PatientRepository.find_anonymized_by_anon_id(anon_id: str) -> Patient | None` (studies+series eager-loaded, `anon_name IS NOT NULL`).
- `StudyRepository.get_all_with_relations(skip=0, limit=100, *, anonymized_only: bool = False)`; `StudyRepository.find_by_anon_uid(anon_uid: str, *, anonymized_only: bool = False) -> Study | None` (patient + series eager-loaded).
- `SeriesRepository.get_all_anonymized(skip=0, limit=100) -> Sequence[Series]` (`Series.study` → `Study.patient` eager-loaded); `SeriesRepository.find_with_relations_by_anon_uid(anon_uid: str, *, anonymized_only: bool = False) -> Series | None` (same eager loads as `get_with_relations:40`); `get_random(*, anonymized_only=False)`; `find_by_criteria(find_query, *, anonymized_only=False)`; `find_by_study_uid(..., *, anonymized_only=False)`.
- "Anonymized" predicate: patient `anon_name IS NOT NULL`; study `anon_uid IS NOT NULL` + its patient anonymized (join `Patient`); series `anon_uid IS NOT NULL` + its study anonymized. Use `col(X).is_not(None)`.

- [ ] **Step 1: Failing tests** (10): `test_auto_id_from_anon_id_parses`, `test_auto_id_from_anon_id_rejects_foreign_prefix_and_garbage`, `test_patients_anonymized_only_excludes_unanonymized`, `test_find_anonymized_by_anon_id_resolves`, `test_find_anonymized_by_anon_id_skips_unanonymized_patient`, `test_studies_anonymized_only_needs_study_and_patient`, `test_study_find_by_anon_uid`, `test_series_anonymized_only_needs_series_and_study`, `test_series_random_anonymized_only`, `test_series_find_by_criteria_anonymized_only`. Use `test_session`, `make_patient`/`make_study`/`make_series` + manual commit; set `anon_name` / `anon_uid` explicitly.
- [ ] **Step 2:** run the file → 10 fail.
- [ ] **Step 3:** implement; defaults keep every existing caller unchanged.
- [ ] **Step 4:** run the file → 10 pass. Commit `feat(repositories): anonymized-only visibility and anon-id lookups`.

---

### Task 3: Masking functions

**Files:**
- Modify: `clarinet/api/masking.py`
- Create test: `tests/test_entity_masking.py`

**Interfaces:**
- Consumes: `Capability` (Task 1), `resolve_capabilities`.
- Produces, all in `clarinet/api/masking.py`:

```python
@dataclass(frozen=True, slots=True)
class EntityAccess:
    unmasked: bool   # holds patients:unmask
    studies: bool    # holds studies:read
    series: bool     # holds series:read
    is_admin: bool   # superuser or 'admin' role

    @classmethod
    def for_user(cls, user: User) -> "EntityAccess": ...

def mask_patient(patient: PatientRead, access: EntityAccess) -> PatientRead
def mask_study(study: StudyRead, access: EntityAccess) -> StudyRead
def mask_series(series: SeriesRead, access: EntityAccess) -> SeriesRead
def mask_series_row(series: Series, access: EntityAccess) -> SeriesBase
```

- Behaviour (each returns a `model_copy`, never mutates):
  - Always: `PatientRead.studies = []` without `access.studies`; nested `series = []` without `access.series`; `SeriesRead.records = []` unless `access.is_admin`.
  - When `not access.unmasked`: field table in `specs/entity-identity-masking/spec.md`; drop nested studies lacking `anon_uid` and nested series lacking `anon_uid`; reuse `_MASKED_STUDY_DATE`. Patient id for a study/series = `Files.per_study_patient_id(study.study_uid)` when `settings.anon_per_study_patient_id` (then nested patient `auto_id` and `anon_name` → `None`), else the patient's `anon_id` / `anon_name`.
  - `mask_series_row` needs `series.study` loaded when masking (guaranteed by Task 2's anonymized repo methods).
- Pass-through allowlist (module constant in the test): patient `{anon_name, auto_id, anon_id}`; study `{anon_uid, modalities_in_study}`; series `{series_description, series_number, modality, instance_count, anon_uid}`.

- [ ] **Step 1: Failing tests** (12): `test_entity_access_for_superuser`, `test_entity_access_for_mapped_role`, `test_mask_patient_swaps_id_and_name`, `test_mask_patient_no_real_identifier_anywhere` (assert the real id/name strings are absent from `model_dump_json()`), `test_mask_study_fields`, `test_mask_study_per_study_mode_uses_hash_and_nulls_patient_anon`, `test_mask_series_fields`, `test_masked_nested_lists_drop_unanonymized`, `test_unmasked_passthrough`, `test_nested_stripped_without_capability`, `test_series_records_emptied_for_non_admin_kept_for_admin`, `test_every_field_is_masked_or_allowlisted` (iterate `PatientRead`/`StudyRead`/`SeriesRead` `model_fields` + computed fields; each name must be in the masked set, the allowlist, or the structural set `{studies, series, patient, study, records}`).
- [ ] **Step 2:** run → 12 fail. **Step 3:** implement. **Step 4:** run → 12 pass.
- [ ] **Step 5:** commit `feat(masking): patient, study and series payload masking`.

---

### Task 4: API guards, service addressing, router wiring

**Files:**
- Modify: `clarinet/api/dependencies.py:516` (after `ReportsAccessDep`), `clarinet/services/study_service.py:68-95,188-228,284-320,353`, `clarinet/api/routers/study.py` (whole file)
- Create tests: `tests/integration/test_entity_capabilities.py`, `tests/integration/test_entity_masking_api.py`

**Interfaces:**
- Consumes: Task 1-3 names.
- Produces in `dependencies.py`: `PatientsReadDep`, `PatientsWriteDep`, `StudiesReadDep`, `StudiesWriteDep`, `SeriesReadDep`, `SeriesWriteDep` (each `Annotated[User, Depends(require_capability(Capability.X))]`); `get_entity_access(user: CurrentUserDep) -> EntityAccess`; `EntityAccessDep`.
- Produces in `study_service.py`: `type Addressing = Literal["literal", "anon", "literal_then_anon"]`. Read-by-id methods gain `*, addressing: Addressing = "literal"` (`get_patient`, `get_study`, `get_study_series`, `get_series`); list methods gain `*, anonymized_only: bool = False` (`get_all_patients`, `get_all_studies`, `get_all_series`, `get_random_series`, `find_series`). Defaults keep every internal caller unchanged.
  - `"anon"`: resolve only via the Task 2 anon lookups with `anonymized_only=True`; miss → the entity's `*NotFoundError`. `get_patient` must not run `validate_patient_id`-then-literal first.
  - `"literal_then_anon"`: existing literal lookup; on `*NotFoundError` try the anon lookup (study/series with `anonymized_only=False`; the patient lookup is anonymized-only by construction — an anon id only reaches a client for an anonymized patient); still missing → re-raise the original error.
- Router rule: `addressing = "literal_then_anon" if access.unmasked else "anon"`; `anonymized_only = not access.unmasked`.

- [ ] **Step 1: Failing tests** `test_entity_capabilities.py` (11): `test_every_route_has_capability_dependency` (iterate `study.router.routes`; each route's dependant tree must contain a callable produced by `require_capability` — mark them by setting `_require.capability = capability` inside the factory and assert the attribute); `test_patients_read_admits_list_denies_delete`; `test_patients_write_admits_create`; `test_no_capability_denied_everywhere` (parametrized over all 18 rows of the endpoint table → counts as 1 name, 18 cases); `test_areas_are_independent`; `test_studies_read_admits_list_and_detail`; `test_series_read_admits_list_random_detail_find_and_study_series`; `test_studies_write_admits_delete`; `test_series_write_admits_create`; `test_admin_role_admitted_everywhere`; `test_me_includes_entity_capabilities` (`AUTH_ME`); `test_service_token_admitted` — `unauthenticated_client` fixture (the other clients override `current_active_user`, which is where the token resolves: `auth_config.py:512-524`), `monkeypatch.setattr("clarinet.api.auth_config.settings.effective_service_token", "secret-token")`, clear `auth_config._service_user_cache`, seed the user `_get_service_user` selects (`auth_config.py:480-501`), send `X-Internal-Token: secret-token` to `GET /patients` → 200.
  Collected count: 11 names + 18 parametrized cases = **29**.
- [ ] **Step 2: Failing tests** `test_entity_masking_api.py` (14): `test_masked_list_hides_unanonymized_patient`, `test_masked_fetch_by_anon_id`, `test_masked_fetch_by_real_id_is_404`, `test_masked_patient_payload_has_no_real_identifier`, `test_masked_study_by_anon_uid`, `test_masked_study_by_real_uid_is_404`, `test_masked_series_by_anon_uid`, `test_masked_random_series_is_anonymized`, `test_unmasked_non_superuser_anon_fallback`, `test_unmasked_literal_id_wins_over_anon_shape` (patient whose real id is `f"{prefix}_{other.auto_id}"`), `test_patient_studies_empty_without_studies_read`, `test_series_records_empty_for_non_admin`, `test_series_find_record_criteria_forbidden_for_non_admin`, `test_series_find_record_criteria_allowed_for_admin`.
- [ ] **Step 3:** run both files → 43 collected; the capability-holder and masking tests fail (403 from the router-wide admin dep / missing behaviour), admin and service-token cases may already pass.
- [ ] **Step 4: Implement `dependencies.py`** — six aliases, `get_entity_access`, and the `_require.capability` marker.
- [ ] **Step 5: Implement `study_service.py`** per Interfaces.
- [ ] **Step 6: Implement `study.py`** — delete `dependencies=[Depends(current_admin_user)]` (`:52`); add the capability dep + `access: EntityAccessDep` per endpoint per the table in `specs/entity-access-capabilities/spec.md`; read endpoints return `mask_*(XRead.model_validate(entity), access)`. `GET /series`, `GET /series/random`, `GET /studies/{uid}/series` switch `response_model` from `Series` to `SeriesBase` and return `mask_series_row(...)` (same field set — a table model cannot be safely `model_copy`-masked). `POST /series/find`: `if find_query.records and not access.is_admin: raise FORBIDDEN`.
- [ ] **Step 7:** run both new files → 43 pass. Run the second baseline selector → 62 pass, files unedited.
- [ ] **Step 8:** `./scripts/run_tests.sh -k "series or study or patient" -q` to catch tests that mocked the old service signatures; fix callers, not contracts.
- [ ] **Step 9:** commit `feat(api): capability guards and masked reads for patients, studies, series`.

---

### Task 5: Records search by anon study / series uid

**Files:**
- Modify: `clarinet/repositories/record_repository.py:1431-1445`
- Create test: `tests/integration/test_record_search_anon_uid.py`

**Interfaces:** no signature change. For `criteria.role_names is not None` (non-superuser), `study_uid` matches `Record.study_uid == v OR Study.anon_uid == v`; `series_uid` likewise against `Series.anon_uid`. Superusers stay literal. Skip the OR branch when the explicit `anon_study_uid` / `anon_series_uid` criterion is also set — `_apply_anon_uid_filter` already joins that table and a second join raises "ambiguous column" (same trap documented at `:1400`).

- [ ] **Step 1: Failing tests** (4): `test_non_superuser_finds_records_by_anon_study_uid`, `test_non_superuser_finds_records_by_anon_series_uid`, `test_anon_uid_search_stays_role_scoped` (a record of a role the caller lacks is absent), `test_superuser_study_uid_is_literal`. Records via `RecordFactory.create_record_with_relations`.
- [ ] **Step 2:** run → 3 fail, 1 passes (superuser literal already true). **Step 3:** implement. **Step 4:** run → 4 pass.
- [ ] **Step 5:** `./scripts/run_tests.sh -k "find" -q` — no regressions in existing record search tests. Commit `feat(records): match anon study and series uids for non-superusers`.

---

### Task 6: Frontend — permissions, route guards, navigation

**Files:**
- Modify: `clarinet/frontend/src/utils/permissions.gleam:19-27`, `clarinet/frontend/src/router.gleam:119-146`, `clarinet/frontend/src/components/layout.gleam:33-99`
- Test: `clarinet/frontend/test/capabilities_test.gleam`

**Interfaces — Produces:** `permissions.patients_read_capability` … `series_write_capability`, `permissions.patients_unmask_capability` (seven `pub const`); `permissions.is_reports_only` = non-admin whose `capabilities == ["reports"]`. `router.requires_capability`: `Patients(_) | PatientDetail(_)` → `patients:read`; `PatientNew` → `patients:write`; `Studies(_) | StudyDetail(_)` → `studies:read`; `SeriesDetail(_)` → `series:read`. Those six leave `requires_admin_role`. `main.must_redirect` (`main.gleam:1116`) needs no change.

- [ ] **Step 1: Failing tests** (9): `requires_capability_patients_test`, `requires_capability_patient_new_is_write_test`, `requires_capability_studies_and_series_test`, `entity_routes_not_admin_gated_test`, `records_route_still_admin_gated_test`, `reports_only_true_for_sole_reports_test`, `reports_only_false_with_patients_read_test`, `nav_routes_mixed_capabilities_test` (user with `patients:read` + `reports` → exactly Patients, Reports, Quarto Reports), `nav_routes_admin_sees_all_test` (9 links, today's order).
- [ ] **Step 2:** from `clarinet/frontend/`: `gleam test` → the 9 fail (gleeunit has no per-module filter — read the report).
- [ ] **Step 3:** implement `permissions.gleam` + `router.gleam`.
- [ ] **Step 4:** `layout.gleam`: replace the `case is_admin(model)` block with `pub fn nav_routes(user: Option(User)) -> List(#(router.Route, i18n.Key))` (pure, so the two nav tests can call it) mapped through `nav_link`; `None` → `[]`. Visibility: `is_admin` for Records, Record Types, Workflow, Activity, Admin; `has_capability` for Patients (`patients:read`), Studies (`studies:read`), Reports + Quarto Reports (`reports`). Keep link order as today. Delete the now-unused `reports_only` helper in this file if nothing else calls it.
- [ ] **Step 5:** `gleam test` → pass; `make frontend-check`. Commit `feat(frontend): capability route guards and per-link navigation`.

---

### Task 7: Frontend — entity links, write actions, page sections

**Files:**
- Modify: `clarinet/frontend/src/components/entity_link.gleam:46-80`, `clarinet/frontend/src/components/records_list.gleam`, `clarinet/frontend/src/pages/records/execute.gleam`, `clarinet/frontend/src/pages/patients/list.gleam:84`, `clarinet/frontend/src/pages/patients/detail.gleam:85,371-411,448`, `clarinet/frontend/src/pages/studies/detail.gleam:174-203`

**Interfaces:** `entity_link.patient_if_admin` / `study_labeled_if_admin` / `series_labeled_if_admin` → `patient_if_allowed(patient_id, allowed: Bool)` etc. The `_if_admin` grep (validated: exactly `entity_link.gleam`, `records_list.gleam`, `pages/records/execute.gleam`) is the file list; callers pass `permissions.has_capability(user, permissions.patients_read_capability)` and the studies/series equivalents.

- [ ] **Step 1:** rename the three helpers + update both callers; `make frontend-check`.
- [ ] **Step 2:** write actions: "New Patient" link (`patients/list.gleam:84`), patient Delete (`detail.gleam:397`) and Anonymize (`:448`) behind `patients:write`; study Delete (`studies/detail.gleam:202`) behind `studies:write`. "Add Record" keeps its `is_admin` guard.
- [ ] **Step 3:** `patients/detail.gleam`: for non-admins, `init` must not start the activity feed (`activity_feed.init` at `:85` fires a request to an `AdminUserDep` endpoint) nor any PACS effect — return `effect.none()` for both; `render_detail` omits `pacs_section` and `activity_section` for non-admins and `studies_section` without `studies:read`. `init` already receives `shared` — read `shared.user` there.
- [ ] **Step 4:** `studies/detail.gleam`: series rows render plain text instead of a link without `series:read`.
- [ ] **Step 5:** `make frontend-check`; `gleam test` → still green. Commit `feat(frontend): capability-aware links, actions and page sections`.

---

### Task 8: Frontend — series detail on the scoped records bucket

**Files:**
- Modify: `clarinet/frontend/src/cache/bucket.gleam:31-32,93-111,144-145`, `clarinet/frontend/src/cache.gleam` (`bucket_key_to_filter`), `clarinet/frontend/src/pages/series/detail.gleam:170`
- Test: `clarinet/frontend/test/` — bucket test file (add to the existing bucket test module if one exists, else `bucket_series_test.gleam`)

**Interfaces — Produces:** `RecordsQuery.series_uid: Option(String)` (default `None`); `bucket.query_with_series(series_uid: String) -> RecordsQuery`; `key_to_topic` gains `optional_part("series", q.series_uid)`; `bucket_key_to_filter` maps it to the `series_uid` body field of `POST /records/find` (backend `RecordSearchFilter.series_uid` already exists). Follow `.claude/rules/frontend-routing-forms.md` §8.5 "Adding a new filter dimension" steps 2-4 only — this is not a user-facing filter, so skip `record_filters` keys and dropdowns.

- [ ] **Step 1: Failing tests** (2): `topic_differs_with_series_uid_test`, `query_with_series_sets_only_series_test`. `gleam test` → fail.
- [ ] **Step 2:** implement `bucket.gleam` + `cache.gleam`. Adding a field to `RecordsQuery` breaks every constructor call — compile errors are the file list.
- [ ] **Step 3:** `series/detail.gleam`: `init` emits `shared.FetchBucket(bucket.Records(bucket.query_with_series(series_uid)))`; the records section reads `cache.bucket_items` / `cache.bucket_status` exactly as `studies/detail.gleam:53,168` does (spinner on `Cold | Loading`, banner on `Failed`). Remove the `s.records` read.
- [ ] **Step 4:** `gleam test` → pass; `make frontend-check`; `make frontend-build`. Commit `feat(frontend): series detail reads the role-scoped records bucket`.

---

### Task 9: Docs, THEORY reconciliation, final verification

**Files:**
- Modify: `clarinet/api/CLAUDE.md` (Router Auth Levels: `study.py` row), `.claude/rules/api-deps.md` (RBAC deps + TOML example), `.claude/rules/api-urls.md` (capability per Patients/Studies/Series row; `SeriesBase` on the three series list endpoints), `docs/kb/domain-model.md` (RBAC section)

- [ ] **Step 1:** docs. `domain-model.md` gets: the vocabulary table, write-implies-read, the three startup rules, masking + addressing, "records never ride on series". Keep rule files operational, the KB page conceptual.
- [ ] **Step 2: Settle the THEORY** in `design.md` ("admin-role non-superusers get 404 on `GET /patients/{anon_id}`"): `git worktree add /tmp/claude-base "$(git merge-base main HEAD)"`, copy only `test_unmasked_non_superuser_anon_fallback` adapted to an `admin`-role user into it, run it there. Fails → record `pre-existing (verified at <sha>)` in `design.md`; passes → record it refuted. Remove the temp worktree.
- [ ] **Step 3:** `make check`.
- [ ] **Step 4:** `timeout 300 make test-unit > /tmp/test-<worktree>.txt 2>&1` (run in background, wait for the notification). Any failure not caused by this branch: reproduce at the merge-base before calling it pre-existing (`~/.claude/rules/unverified-claims.md`).
- [ ] **Step 5:** confirm the gates: 24 (capability unit files), 62 (study CRUD + client, unedited), 10 + 12 + 29 + 14 + 4 new backend tests, `gleam test` green.
- [ ] **Step 5b: Manual smoke** (view-level scenarios have no unit test): `make run-dev` with `[role_capabilities] coord = ["patients:read"]`, log in as a `coord` user — nav shows only Patients; patient detail shows no Delete / Anonymize / Add Record / PACS / Activity, fires no 403 (browser network tab), records section lists only role-permitted records; `/studies` redirects to the dashboard.
- [ ] **Step 6:** grep the change directory for `THEORY` / `UNRECONCILED` — every hit verified or refuted.
- [ ] **Step 7:** commit `docs: capability-gated patients, studies and series`. Then `Agent(pr-diff-reviewer)` with `proposal.md` + the three specs as acceptance criteria, before the first `gh pr create`.
