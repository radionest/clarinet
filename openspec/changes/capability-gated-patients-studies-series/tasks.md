## 1. Capability vocabulary and config validation

- [ ] 1.1 Add the seven entity members to `Capability` (`clarinet/models/capability.py`); extend `tests/test_capabilities.py` for the admin-holds-all case
- [ ] 1.2 Write-implies-read closure in `resolve_capabilities`, applied after the role union; test the write-only grant scenario
- [ ] 1.3 Three per-role rules in `validate_role_capabilities` on the implied closure (writers need unmask; unmask needs a read; per-study mode needs unmask for `patients:*`); extend `tests/test_bootstrap_capabilities.py` with every scenario in `specs/entity-access-capabilities`
- [ ] 1.4 Update the `role_capabilities` comment in `clarinet/settings.py`

## 2. Repository visibility and addressing

- [ ] 2.1 `PatientRepository`: anonymized-only list filter and lookup by anon id (`auto_id` parsed from `{prefix}_N`, reuse the parse at `record_repository.py:1404`)
- [ ] 2.2 `StudyRepository`: `find_by_anon_uid`, anonymized-only filter (study `anon_uid` set and patient anonymized), nested series filtered
- [ ] 2.3 `SeriesRepository`: anonymized-only filter on `get_all` / random / `find` (series `anon_uid` set and study anonymized)
- [ ] 2.4 Repository tests for 2.1-2.3: un-anonymized rows excluded, anon lookups resolve, unknown anon id returns nothing

## 3. Masking

- [ ] 3.1 `mask_patient`, `mask_study`, `mask_series` in `clarinet/api/masking.py` per the field table in `specs/entity-identity-masking`; reuse `_MASKED_STUDY_DATE`
- [ ] 3.2 Nested-data stripping by capability (`studies` without `studies:read`, `series` without `series:read`) and `SeriesRead.records` emptied for non-admins
- [ ] 3.3 Masking unit tests, including the pass-through-field allowlist test that fails on an unclassified field

## 4. API guards and service wiring

- [ ] 4.1 Capability dependency aliases beside `ReportsAccessDep` in `clarinet/api/dependencies.py`, plus a per-request `unmasked` flag dependency
- [ ] 4.2 `clarinet/api/routers/study.py`: remove the router-wide admin dependency; per-endpoint guards per the table in `specs/entity-access-capabilities`
- [ ] 4.3 `StudyService` read methods take the masked flag: masked caller = anon-only addressing + anonymized-only filter; unmasked caller = literal first, anon fallback second
- [ ] 4.4 Routers apply the mask functions; `POST /series/find` with `records` criteria from a non-admin responds 403
- [ ] 4.5 Integration tests: capability matrix (403/200) per endpoint, areas independent, service token admitted, route-enumeration test asserting every route has a capability dependency
- [ ] 4.6 Integration tests: masked caller (hidden rows, real id 404, anon addressing, masked fields, nested stripping), unmasked anon fallback, literal-id-wins
- [ ] 4.7 Settle the THEORY in `design.md` (admin-role non-superuser 404 on `GET /patients/{anon_id}`): run the 4.6 fallback test at `git merge-base main HEAD` and record `pre-existing (verified at <sha>)` or refute it

## 5. Records search by anon uid

- [ ] 5.1 `RecordRepository._build_criteria_query`: for non-superusers (`role_names is not None`) `study_uid` / `series_uid` match the real uid or `anon_uid`; superusers stay literal
- [ ] 5.2 Tests: records by anon study uid and anon series uid stay role-scoped; superuser literal match

## 6. Frontend

- [ ] 6.1 `utils/permissions.gleam`: seven capability constants; `is_reports_only` = reports is the only capability
- [ ] 6.2 `router.gleam`: move the six routes from `requires_admin_role` to `requires_capability`
- [ ] 6.3 `components/layout.gleam`: flat per-link visibility list replacing the admin / reports-only branch
- [ ] 6.4 `components/entity_link.gleam`: `*_if_admin` to `*_if_allowed`; update every caller (grep `_if_admin` first and treat the result as the file list)
- [ ] 6.5 Write actions behind `:write` in `pages/patients/list.gleam`, `pages/patients/detail.gleam`, `pages/studies/detail.gleam`
- [ ] 6.6 `pages/patients/detail.gleam`: PACS section and activity feed neither rendered nor initialised for non-admins; studies section hidden without `studies:read`; `pages/studies/detail.gleam` series rows plain text without `series:read`
- [ ] 6.7 `cache/bucket.gleam` + `cache.gleam`: `series_uid` on `RecordsQuery`, `query_with_series`, `key_to_topic`, `bucket_key_to_filter`; `pages/series/detail.gleam` reads the bucket
- [ ] 6.8 gleeunit tests beside `capabilities_test.gleam`: `requires_capability` per route, `is_reports_only` with mixed capabilities, bucket topic includes series
- [ ] 6.9 `make frontend-check`, `gleam test`, `make frontend-build`

## 7. Docs and verification

- [ ] 7.1 `clarinet/api/CLAUDE.md` auth table (`study.py` row), `.claude/rules/api-deps.md` (new deps + TOML example), `.claude/rules/api-urls.md` (capability per patients/studies/series row)
- [ ] 7.2 `docs/kb/domain-model.md` RBAC section: vocabulary, write-implies-read, startup rules, masking and addressing
- [ ] 7.3 `make check`; `timeout 300 make test-unit > /tmp/test-<worktree>.txt 2>&1`; confirm existing `tests/integration/test_study_crud.py` and `tests/test_client.py` pass unchanged
- [ ] 7.4 Grep the change directory for `THEORY` / `UNRECONCILED`; every hit verified or refuted before the branch is done
