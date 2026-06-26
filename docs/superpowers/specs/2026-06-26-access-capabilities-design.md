# Capability-based access control (reports first)

- **Date:** 2026-06-26
- **Status:** Approved design — ready for implementation plan
- **Scope:** Core `clarinet` framework + consuming project `clarinet_nir_liver` (settings only)

## Problem

Access control in Clarinet is role-based with a hardcoded two-level model: `superuser`
bypasses everything, and the built-in `admin` role unlocks the entire admin surface.
Both SQL reports and Quarto reports live under `/api/admin/*` and are gated by
`AdminUserDep` (`is_superuser OR "admin" in role_names`,
`clarinet/api/dependencies.py:474-485`). This is all-or-nothing: a user either is an
admin and can see studies, patients, users **and** reports, or is not an admin and
cannot see reports at all.

Downstream projects (e.g. `clarinet_nir_liver`) need a role that can reach **only** the
reports area without being granted the full admin surface. The authorization logic is
currently not extensible from the `plan/` package or settings.

## Goal

Introduce a thin **capability** layer over roles so a project can declare, in its own
`settings.toml`, a role whose holders may access the reports feature and nothing else
from the admin surface. Reports is the first capability; the mechanism is reusable for
future feature areas without new tables or admin screens.

### Non-goals

- No general permission matrix, no DB-backed permission tables, no admin UI for
  managing permissions (explicitly rejected during brainstorming as over-engineering).
- No per-report access control — one `reports` capability covers all SQL and Quarto
  reports in a project (feature-level, not report-level).
- No "restricted admin": the `admin` role and `superuser` keep full access to everything,
  including all capabilities.

## Model

A **capability** is a named, coarse-grained permission string for a feature area. The
core framework owns a **closed vocabulary** of known capabilities (for fail-fast typo
detection). The initial vocabulary is `{"reports"}`.

A project declares a **role → capabilities** mapping in `settings.toml`. The
**effective capabilities** of a user are:

- `superuser` or member of the `admin` role → **all** known capabilities (D2);
- otherwise → the union of capabilities mapped to the user's roles.

Endpoints are guarded by `require_capability("reports")` instead of `AdminUserDep`.
Because admins/superusers resolve to all capabilities, every existing admin keeps access
unchanged — this is the backward-compatibility guarantee.

### Resolved decisions

- **D1 — URL paths stay `/api/admin/reports` and `/api/admin/quarto-reports`.** Only the
  guard changes. The `admin` segment becomes a historical name; renaming would churn the
  frontend client and test URL constants for no functional gain.
- **D2 — `admin`/`superuser` implicitly hold every capability.** Zero regression for
  existing deployments.
- **D3 — roles named as keys of `role_capabilities` are auto-created at startup**, so a
  project does not have to list them in both `extra_roles` and `role_capabilities`.
- **D4 — a non-admin user who holds the `reports` capability lands on the reports page**
  after login instead of an admin page they cannot use.

## Division of work

The mechanism is added to **core `clarinet`**. The consuming project
`clarinet_nir_liver` only adds a few lines to its `settings.toml`:

```toml
[role_capabilities]
analyst = ["reports"]
```

No fork of the framework, no Python in the `plan/` package for this feature.

## Backend changes (core `clarinet`)

1. **Capability vocabulary** — new module `clarinet/models/capability.py`:
   - `class Capability(StrEnum): REPORTS = "reports"`
   - `KNOWN_CAPABILITIES: frozenset[str] = frozenset(c.value for c in Capability)`
   - `StrEnum` gives type-safety in code and plain strings for settings / JSON
     serialization (same pattern as `ReportFormat`).

2. **Resolver** — new module `clarinet/services/authorization.py`:
   - `resolve_user_capabilities(user: User) -> set[str]`
     - `if user.is_superuser or "admin" in user.role_names: return set(KNOWN_CAPABILITIES)`
     - else union of `settings.role_capabilities.get(role, [])` over `user.role_names`.
   - Pure function of the user + `settings`; unit-testable without a request.

3. **Dependency factory** — in `clarinet/api/dependencies.py`, next to `current_admin_user`:
   - `require_capability(cap: str) -> Callable[..., Awaitable[User]]` returning a dependency
     that takes `CurrentUserDep`, returns the user when `cap in resolve_user_capabilities(user)`,
     else raises `HTTPException(status_code=403, detail=...)` (mirrors `current_admin_user`).
   - Alias: `ReportsAccessDep = Annotated[User, Depends(require_capability(Capability.REPORTS))]`.

4. **Setting** — `clarinet/settings.py`:
   - `role_capabilities: dict[str, list[str]] = {}`
   - Parsed from a `[role_capabilities]` TOML table; env override
     `CLARINET_ROLE_CAPABILITIES` as JSON, consistent with existing settings.
   - Confirm the exact pydantic-settings declaration mirrors `extra_roles` during planning.

5. **Startup validation + role auto-creation** — `clarinet/utils/bootstrap.py`:
   - In `add_default_user_roles()` (`:27-50`), extend the created role set from
     `default_roles + settings.extra_roles` to also include
     `settings.role_capabilities.keys()` (D3), deduped via the existing
     `dict.fromkeys(...)` idiom.
   - Add a fail-fast validation (new helper, e.g. `validate_role_capabilities()`) that
     raises `ConfigurationError` if any value in `role_capabilities` is not in
     `KNOWN_CAPABILITIES`. Follow the message style of the role/viewer validators in
     `reconcile_config` (`bootstrap.py:374-419`): list the unknown names plus the known
     set. Wire it into the lifespan near the existing config validation so a typo
     (`"reprots"`) refuses startup instead of silently denying access.

6. **Swap guards** — replace `AdminUserDep` with `ReportsAccessDep`:
   - `clarinet/api/routers/reports.py` — `list_reports` (`:36`), `download_report` (`:50`).
   - `clarinet/api/routers/quarto_reports.py` — `list_quarto_reports` (`:28`),
     `render_quarto_report` (`:43`), `get_quarto_render_status` (`:58`),
     `download_quarto_render` (`:69`).
   - The internal-token / service flow (Quarto fetching SQL data over the loopback
     client) must keep working: confirm the service token still resolves to an admin and
     therefore to all capabilities.

7. **Expose capabilities to the frontend** — the current-user serialization must include
   `capabilities: list[str]`:
   - Capabilities derive from `settings`, not from a DB column, so they cannot be a pure
     SQLModel computed field. Compute via `resolve_user_capabilities` when building the
     read model returned by the SPA's bootstrap endpoint(s) (`GET /api/auth/me` and/or
     `GET /api/user/me`).
   - Identify the exact `UserRead`/serialization path and the single endpoint the SPA
     calls on boot during planning; add a `capabilities` field there.

## Frontend changes (Gleam)

8. **User model** — add `capabilities: List(String)` to `User` in
   `clarinet/frontend/src/api/models.gleam` (`:142-154`) and decode it from `/me`.

9. **Permission helper** — `clarinet/frontend/src/utils/permissions.gleam`:
   - `has_capability(user, cap) -> Bool` — plain membership check (the server already
     includes all capabilities for admins, so no client-side admin special-casing).

10. **Routing** — `clarinet/frontend/src/router.gleam` (`:109-137`):
    - Move the reports route(s) out from under `requires_admin_role`.
    - Introduce a `required_capability(route) -> Option(String)` (or equivalent); the
      guard in `main.gleam` admits a route when the user is admin **or** holds the
      route's required capability.

11. **Navigation** — `components/layout.gleam`: show the "Reports" nav entry when
    `has_capability(user, "reports")` (admins included); the rest of the admin nav stays
    hidden for a reports-only role.

12. **Landing (D4)** — in `main.gleam` post-login routing: a user who is not admin but
    holds `reports` is redirected to the reports page instead of Home/Studies.

## Backward compatibility

- Admins and superusers keep identical access (they resolve to all capabilities).
- A non-admin without any mapped capability still gets 403 on reports — unchanged.
- The only new behavior: a non-admin role that maps to `reports` now passes the guard.
- **No database migration** — roles already exist as a table; capabilities live in
  settings. This is a deliberate property of the chosen approach.

## Testing

- **Unit:** `resolve_user_capabilities` (superuser → all; admin → all; mapped role →
  its caps; unmapped role → empty); `require_capability` allow vs. deny (403);
  `validate_role_capabilities` rejects an unknown capability with `ConfigurationError`.
- **API:** with `role_capabilities = {analyst: [reports]}` and a user holding only
  `analyst`:
  - `GET /api/admin/reports` and `/api/admin/reports/{name}/download` → 200;
  - all four `/api/admin/quarto-reports*` endpoints → 200;
  - `/api/admin/stats`, studies, users → 403;
  - admin/superuser → 200 everywhere (regression guard);
  - plain user with no capability → 403 on reports.
- **Cascade check** (per `clarinet/api/CLAUDE.md`): changing auth levels ripples into
  `tests/test_client.py`, `tests/integration/test_study_crud.py`, and e2e fixtures.
  Audit whether any existing test asserts "non-admin → 403 on reports" as an invariant;
  such a test must move to "non-admin **without the capability** → 403".

## Documentation to update

- `clarinet/api/CLAUDE.md` and `.claude/rules/api-deps.md` — auth-level tables: reports
  routers are now capability-gated, not `AdminUserDep`.
- `clarinet/settings.py` docs / settings reference — `role_capabilities` and
  `CLARINET_ROLE_CAPABILITIES`.
- A short example in the project-setup rule (`.claude/rules/project-setup.md`) showing
  the `[role_capabilities]` block.

## Open items to finalize during planning

- Exact pydantic-settings declaration for the `dict[str, list[str]]` setting and its env
  parsing (mirror `extra_roles`).
- The precise `UserRead` serialization path and the single SPA bootstrap endpoint to
  carry `capabilities`.
- Whether `require_capability` lives best as a closure in `dependencies.py` or as a small
  class dependency; pick the form that matches existing factory patterns.

## Out of scope

- Per-report visibility, restricted-admin roles, DB-backed permissions, and any admin UI
  for editing role/capability mappings. These can build on this foundation later if a
  real need appears.
