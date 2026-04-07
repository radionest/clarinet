# Frontend Development Guide

Gleam + Lustre SPA. MVU (Model-View-Update) architecture. Single JS bundle served by FastAPI at `/static/clarinet_frontend.js`.

Detailed MVU page contract, `OutMsg` reference, effect patterns, and pitfalls live in `.claude/rules/frontend.md` (auto-loaded when editing `clarinet/frontend/src/**/*.gleam`). This file is the high-level overview.

## Stack

- **Lustre** — web framework (MVU). Pages are self-contained MVU modules.
- **Modem** — client-side routing. All paths go through `config.base_path()` (sub-path deploy).
- **gleam_fetch** — HTTP. Session cookies attached automatically; no manual token handling.
- **formosh** — form web-component (private repo `git@github.com:radionest/gleam_formosh.git`).
- **plinth** — DOM / browser FFI (window, global timers, localStorage).

Full dep list in `gleam.toml`. Test runner: `gleeunit`.

## Architecture Overview

```
src/
├── clarinet_frontend.gleam  # Bundle entry (must match package name)
├── main.gleam               # App root: init, update dispatch, view dispatch, route → page wiring
├── router.gleam             # Route type, parse_route, route_to_path, auth/admin guards
├── store.gleam              # Model (global state), Msg (app-level), PageModel union
├── shared.gleam             # Shared (read-only context) + OutMsg (page → main commands)
├── cache.gleam              # Self-contained MVU for entity caches (studies/records/...)
├── preload.gleam            # Self-contained MVU for viewer preload modal
├── config.gleam             # base_path() — reads sub-path prefix at boot
├── api/                     # HTTP clients (one file per backend resource) + models.gleam + types.gleam
├── components/              # Reusable UI: layout.gleam, forms/ (base, patient_form, record_form)
├── pages/                   # Self-contained MVU page modules (see contract below)
└── utils/                   # load_status, logger, dom, permissions, viewer, status
```

## Page Module Contract

Every page under `src/pages/` is a self-contained MVU module exposing **exactly** this public API:

```gleam
pub type Model { ... }
pub type Msg { ... }

pub fn init(args, _shared: Shared) -> #(Model, Effect(Msg), List(OutMsg))
pub fn update(model, msg, _shared: Shared) -> #(Model, Effect(Msg), List(OutMsg))
pub fn view(model, shared: Shared) -> Element(Msg)
```

The **third element** of the `init`/`update` tuple is the key idea: pages never touch global state directly. They emit `OutMsg` values that `main.gleam` translates into store mutations (cache updates, navigation, toasts, modals, etc.).

Reference implementations:
- **Stateless view over cache** — `pages/studies/list.gleam` (init just fires `ReloadStudies`, view reads `shared.cache.studies`)
- **Load + mutate + PACS ops** — `pages/patients/detail.gleam` (full `LoadStatus`, async actions, many `OutMsg` types)
- **Page with timers / cleanup** — `pages/records/execute.gleam` (exports `cleanup(model) -> Effect(Msg)` for `main.gleam` to call on route change)

## Adding a New Page — Checklist

1. Add the variant in `router.gleam`: `Route` type, `route_to_path`, `parse_route`, and `requires_auth` / `requires_admin_role` / `section` if applicable.
2. Create `pages/<area>/<name>.gleam` implementing the MVU contract above.
3. In `store.gleam`: add `NewPageModel(page.Model)` to `PageModel` union and `NewPageMsg(page.Msg)` to `Msg`.
4. In `main.gleam`:
   - Import the page module with an alias (`import pages/area/name as area_name_page`).
   - Add a `store.NewPageMsg(...) -> delegate_page_update(...)` arm to the top-level `update`.
   - Add a branch to `init_page_for_route` mapping `router.NewRoute(...) -> init_page(model, page.init(arg, _), ...)`.
   - Add a branch to `view_content`'s page dispatch calling `element.map(page.view(pm, shared), store.NewPageMsg)`.
   - If the page owns timers/subscriptions, add it to `cleanup_current_page` and export `pub fn cleanup(model) -> Effect(Msg)` from the page.
5. Rebuild: `make frontend-build`.

Everything is type-checked end-to-end — forgetting one of steps 3/4 is a compile error, not a runtime surprise.

## Building

```bash
make frontend-build      # Production (minified), output: clarinet/static/clarinet_frontend.js
make frontend-deps       # Install Gleam deps
```

Dev iteration (from `clarinet/frontend/`): `gleam run -m lustre/dev build`.

Requires `bun` system-wide. FastAPI serves the bundle when `frontend_enabled=True` (default). Set `frontend_enabled=False` for API-only mode.

## Auth (Frontend Contract)

- Auth state = `Option(User)` in `store.Model.user`. Cookie `clarinet_session` is attached automatically by `gleam_fetch`.
- On boot, `main.init` calls `auth.get_current_user()` (session check). While `checking_session: True`, route-change redirects are suppressed.
- 401 from any API call → `types.AuthError` → pages return `[shared.Logout]` in their `OutMsg` list. `main.handle_api_error` also auto-detects expired sessions and pushes the user to `/login`.
- Logout: `shared.Logout` → `store.Logout` → `auth.logout()` → `reset_for_logout` + `modem.push(Login)`.

Backend contract (FastAPI-users, bcrypt, `AccessToken`) lives in the backend CLAUDE.md — the frontend only cares that the cookie is set/cleared by the server.

## Testing

- Unit tests in `clarinet/frontend/test/` (gleeunit). Run from `clarinet/frontend/`: `gleam test`.
- E2E tests (Playwright) live in `deploy/test/e2e/` — **selectors target real Gleam components**, see `.claude/rules/e2e-tests.md` (auto-loaded for those paths).

## Anti-patterns

- Do **not** import `store` from a page module — use `Shared` + `OutMsg`. Pages must be decoupled from `store.Model` internals.
- Do **not** make HTTP calls directly from a page — go through `api/<resource>.gleam`. If a resource is missing, add it there.
- Do **not** put entity caching inside a page — cache lives in `cache.gleam`. Pages request reloads via `OutMsg` (`ReloadRecords`, `ReloadPatient(id)`, etc.) and read from `shared.cache.*`.
- Do **not** hand-build URLs — always `router.route_to_path(router.SomeRoute(...))`. It prepends `config.base_path()` for sub-path deploys.
- Do **not** bypass `load_status.LoadStatus` for async fetches in detail pages — otherwise a failed load gets stuck on the spinner.
