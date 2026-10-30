## Context

The capability system (`clarinet/models/capability.py`, `settings.role_capabilities`, `require_capability` in `clarinet/api/dependencies.py`) covers only `reports`. Patients, studies and series are admin-only at three layers: a router-wide `Depends(current_admin_user)` in `clarinet/api/routers/study.py:52`, `router.requires_admin_role` in the frontend, and an all-or-nothing nav in `components/layout.gleam`.

Constraints found during exploration:

- Record payloads mask patient identity for every non-superuser (`clarinet/api/masking.py`); `PatientRead` carries the real id/name next to `anon_id`/`auto_id`/`anon_name` — the exact mapping record masking hides.
- `POST /records/find` is role-scoped for non-superusers, and the patient/study detail pages already load records through it. The series detail page does not: it renders the unscoped `SeriesRead.records`.
- The record repository auto-routes an anon-shaped `patient_id` via `auto_id` for non-superusers; `study_uid` / `series_uid` are matched literally.
- `anon_per_study_patient_id=True` exists specifically so non-superusers cannot link one patient's studies.

## Goals / Non-Goals

**Goals:**
- Projects grant patients/studies/series access per role via `settings.role_capabilities`, split read/write.
- A role never receives the real-to-anon identity mapping unless it holds `patients:unmask`.
- Non-admins see only the records their roles permit, on every page.
- Unsafe capability combinations refuse startup.

**Non-Goals:**
- Record masking stays keyed on `is_superuser` + `RecordType.mask_patient_data`; it does not learn about `patients:unmask`.
- PACS search/import (`dicom.py`) and the activity feed (`admin.py`) stay admin-only.
- "Add Record" stays admin-only.
- No DB-backed permission table, no per-row ACLs.

## Decisions

### D1: Seven capabilities, one unmask switch
- **Choice**: `patients|studies|series` × `read|write`, plus `patients:unmask` governing real identifiers in all three payload families. Endpoint mapping: see `specs/entity-access-capabilities`.
- **Rationale**: study and series payloads embed patient identity and real UIDs; a per-area unmask would let `studies:unmask` leak what `patients:unmask` withholds.
- **Alternative**: single `patients` capability — rejected by the user (delete/anonymize too dangerous to bundle with browse).

### D2: `X:write` implies `X:read`
- **Choice**: `resolve_capabilities` adds `X:read` for every granted `X:write`, after the role union. Validation runs on the same closure.
- **Rationale**: user preference — friendlier config than a startup error.
- **Alternative**: fail-fast when write is granted without read — rejected by the user.

### D3: Per-role startup validation
- **Choice**: three rules in `validate_role_capabilities`, each per role on the implied closure: (1) any `*:write` ⇒ `patients:unmask`; (2) `patients:unmask` ⇒ some `*:read`; (3) `anon_per_study_patient_id=True` ⇒ any `patients:*` needs `patients:unmask`.
- **Rationale**: rule 1 means write endpoints only ever see real ids, so they need no anon resolution. Rule 3 is the user's decision: the contradictory config must not exist, rather than being handled by a runtime 403.
- **Per-role, not per-user-union**: a user holding only the offending role would be in the forbidden state.
- **Alternative**: runtime 403 for masked patient access in per-study mode — rejected by the user.

### D4: Masking extends `api/masking.py` (approach A)
- **Choice**: `mask_patient` / `mask_study` / `mask_series` return `model_copy(update=…)` of the ordinary Read DTOs, applied in the router beside `mask_records`. Field table: see `specs/entity-identity-masking`.
- **Per-study mode**: rule 3 (D3) still allows a masked `studies:read` / `series:read` role, so `mask_study` / `mask_series` use the per-study hash (`Files.per_study_patient_id`) as the patient id and null the nested patient's `auto_id` / `anon_name` — the same branch record masking takes. A masked `PatientRead` cannot occur in that mode.
- **Nested lists**: repositories filter top-level rows; the mask functions drop un-anonymized entries from nested `studies` / `series` (they are already eager-loaded).
- **Rationale**: identical to the established records pattern — one DTO, router-applied, one frontend model shape.
- **Alternatives**: separate masked DTOs (doubles models, Gleam decoders, page code paths); serializer-context masking (implicit — a forgotten context is a silent leak).

### D5: Masked callers see anonymized rows only
- **Choice**: repository-level filter for masked callers — patients `anon_name IS NOT NULL`; studies `anon_uid IS NOT NULL` with anonymized patient; series `anon_uid IS NOT NULL` with anonymized study. Same filter on nested lists.
- **Rationale**: no real identifier can reach a masked role; matches record masking dropping series without `anon_uid`.
- **Alternative**: pass un-anonymized rows through unmasked (mirrors records) — rejected: a list exposes the whole anonymization queue, not just rows the user works on.

### D6: Addressing
- **Choice**: masked caller — path id is an anon id only (patient via `auto_id` parsed from `{prefix}_N`, study via new `StudyRepository.find_by_anon_uid`, series via existing `find_by_anon_uid`); a real id yields 404, indistinguishable from unknown. Unmasked caller — literal lookup first, anon fallback second.
- **Rationale**: anon-only for masked callers prevents probing real ids. The unmasked fallback is needed because records stay masked for non-superusers, so their patient links carry anon ids. Literal-first preserves a real PatientID that happens to match `{prefix}_N`.

### D7: Nested data follows capabilities; records never ride on series
- **Choice**: `PatientRead.studies` emptied without `studies:read`; nested `series` emptied without `series:read`; `SeriesRead.records` emptied for every non-admin; non-empty `SeriesFind.records` from a non-admin → 403.
- **Rationale**: nested payloads otherwise bypass the per-area guards, and `SeriesRead.records` bypasses record role-scoping. The 403 closes an existence oracle on records the caller cannot see.
- **Alternative**: remove `SeriesRead.records` for everyone — rejected: admin/`ClarinetClient` consumers may depend on it.

### D8: Records search accepts anon study/series uids
- **Choice**: for non-superusers, `study_uid` / `series_uid` criteria match the real uid column **or** `anon_uid`, parallel to the existing `patient_id` auto-route.
- **Rationale**: masked study/series pages hold only anon uids and must still load their role-scoped bucket.

### D9: Frontend gating
- **Choice**: capability constants in `utils/permissions.gleam`; six routes move from `requires_admin_role` to `requires_capability`; nav becomes a flat per-link visibility list; `entity_link.*_if_admin` → `*_if_allowed`; write buttons behind `:write`; PACS + activity sections neither rendered nor initialised for non-admins; series detail reads the scoped bucket (`series_uid` added to `bucket.RecordsQuery`). `is_reports_only` narrows to "reports is the only capability".
- **Rationale**: `main.must_redirect` already handles `requires_capability` generically; the two-branch nav cannot express a second capability.

## Risks / Trade-offs

- [Risk] A future endpoint added to `study.py` ships with no guard, since the router-wide dependency is gone → Mitigation: a test enumerates the router's routes and asserts each has a capability dependency.
- [Risk] A new PHI-bearing field on `PatientBase`/`StudyBase`/`SeriesBase` is not added to the mask functions → Mitigation: masking tests assert the full masked payload against an explicit allowlist of pass-through fields, so a new field fails the test until classified.
- [Risk] OR-match on `study_uid`/`anon_uid` lets a masked caller confirm a real uid they already know maps to their records → Accepted: they must already hold the real uid, and the records returned are ones they are permitted.
- [Trade-off] Unmask holders still see masked records → accepted; record masking is a non-goal, and the anon fallback (D6) keeps navigation working.
- [Trade-off] `patients:write` grants cascade delete of a patient to a non-admin role → accepted by the user; rule 1 at least forces that role to be explicitly trusted with unmask.
- THEORY: `admin`-role non-superusers today get 404 following a patient link from a masked record. Confirm/refute with the D6 fallback integration test (admin-role non-superuser, anonymized patient, `GET /patients/{anon_id}`) run at the branch base before the fix.

## Migration Plan

- No DB migration; capabilities live in settings.
- Deploy is inert until a project adds the new capabilities to `role_capabilities`. Admin behaviour is unchanged.
- Rollback: revert the release. A `settings.toml` that names the new capabilities must drop them first — the older version rejects unknown capability names at startup.

## Open Questions

None.
