## Why

Patients, studies and series are reachable only by superusers and the `admin` role: the API router carries a router-wide admin dependency and the frontend hides the routes and nav links. Projects that need a coordinator or analyst role to browse patients must hand out full admin. The capability system already decouples feature access from `admin`, but covers only `reports`. Extending it lets each project grant these areas per role in `settings.role_capabilities`, while masking keeps the real-to-anonymized identity mapping away from roles that were not explicitly trusted with it.

## What Changes

**Capability vocabulary**
- From: one capability, `reports`.
- To: plus `patients:read`, `patients:write`, `patients:unmask`, `studies:read`, `studies:write`, `series:read`, `series:write`. `X:write` implies `X:read`.
- Impact: non-breaking; superuser and `admin` still hold everything.

**Patient / study / series API authorization**
- From: router-wide `Depends(current_admin_user)` on `clarinet/api/routers/study.py`.
- To: per-endpoint capability guards.
- Impact: non-breaking for admins and the service token; non-admins with a granted capability gain access.

**Identity masking on patient / study / series payloads**
- From: payloads always carry real identifiers (admin-only surface).
- To: callers without `patients:unmask` get anonymized identifiers, see only anonymized rows, and address entities by anon id only; unmasked callers resolve a literal id first, anon id second.
- Impact: admins unchanged. New behaviour for non-admin capability holders.

**Record visibility through series**
- From: `SeriesRead.records` returns every record of the series; `POST /series/find` accepts record criteria from any caller.
- To: `records` is empty for non-admins; record criteria from non-admins return 403. Records search matches `study_uid` / `series_uid` against the anon uid too for non-superusers.
- Impact: admins unchanged.

**Startup config validation**
- New `ConfigurationError` cases: a role with any `*:write` but no `patients:unmask`; `patients:unmask` with no `*:read`; any `patients:*` without `patients:unmask` while `anon_per_study_patient_id=True`.

**Frontend**
- From: nav is admin / reports-only; six routes require admin; entity links and write buttons keyed on `is_admin`.
- To: per-link nav visibility, capability route guards, `:write`-gated actions, admin-only sections of patient detail hidden for non-admins, series detail records from the role-scoped bucket.

## Capabilities

### New Capabilities
- `entity-access-capabilities`: capability vocabulary for patients/studies/series, write-implies-read resolution, per-endpoint guards, startup validation rules.
- `entity-identity-masking`: masked payload shape, anonymized-only visibility, anon-id addressing, nested-data stripping, record visibility through series, anon-uid records search.
- `frontend-capability-gating`: capability-driven navigation, route guards, write-action visibility, and page sections for non-admin users.

### Modified Capabilities

None — `openspec/specs/` holds only `file-path-safety` and `slicer-overlap-detection`; neither changes.

## Impact

- Backend: `clarinet/models/capability.py`, `clarinet/api/dependencies.py`, `clarinet/api/routers/study.py`, `clarinet/api/masking.py`, `clarinet/services/study_service.py`, `clarinet/repositories/{patient,study,series,record}_repository.py`, `clarinet/settings.py` (comment).
- Frontend: `utils/permissions.gleam`, `router.gleam`, `components/layout.gleam`, `components/entity_link.gleam` + callers, `pages/patients/{list,detail}.gleam`, `pages/studies/detail.gleam`, `pages/series/detail.gleam`, `cache/bucket.gleam`, `cache.gleam`.
- Tests: new capability/masking suites; `tests/utils/urls.py` unchanged (no new URLs).
- Docs: `clarinet/api/CLAUDE.md`, `.claude/rules/api-deps.md`, `.claude/rules/api-urls.md`, `docs/kb/domain-model.md`.
- No DB migration, no new dependency, no new endpoint.
