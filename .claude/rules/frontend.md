---
paths:
  - "clarinet/frontend/src/**/*.gleam"
  - "clarinet/frontend/test/**/*.gleam"
---

# Frontend MVU Reference

Detailed reference for working inside `clarinet/frontend/src/`. Auto-loaded when editing Gleam files there. High-level overview is in `clarinet/frontend/CLAUDE.md`; **that** file + this one are the two sources of truth for the page module contract.

## 1. Page Module Contract (exact signatures)

Every page in `src/pages/**.gleam` is a self-contained MVU module exposing these public symbols:

```gleam
// Per-page state — no imports from `store`
pub type Model { ... }

// Page-local messages — flat enum, prefix-unique (never clash with other pages)
pub type Msg { ... }

// Called once by main.init_page_for_route. Args are URL params (id, name, ...).
pub fn init(
  args,                              // zero or more URL params; omitted for param-less pages
  shared: Shared,                    // read-only global context; prefix _ if unused
) -> #(Model, Effect(Msg), List(OutMsg))

// Called via delegate_page_update when the wrapped Msg arrives. shared is rebuilt fresh per call.
pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg))

// Rendered inside view_content. Use element.map in main — done automatically by the dispatcher.
pub fn view(model: Model, shared: Shared) -> Element(Msg)
```

Optional (page owns a long-lived resource — timer, subscription, viewer handle):

```gleam
// Called by main.cleanup_current_page when the user navigates away.
// Return effect.none() if there is nothing to clean up.
pub fn cleanup(model: Model) -> Effect(Msg)
```

If you add `cleanup`, you MUST also add a branch in `main.cleanup_current_page` that pattern-matches the page's `store.PageModel` variant and wraps `cleanup` with `effect.map(_, store.YourPageMsg)`. Forgetting this silently leaks the resource.

Private helpers: keep everything else `fn` (lowercase, unexported). Exporting more than the four/five symbols above breaks encapsulation and makes refactors harder.

## 2. Shared / OutMsg Protocol

Pages never touch `store.Model` directly. They receive a `Shared` snapshot and emit `OutMsg` values; `main.apply_out_msgs` is the single translator.

### `shared.Shared` fields (read-only)

```gleam
pub type Shared {
  Shared(
    user: Option(User),           // current session user; None if logged out
    route: Route,                 // current route (for active-tab highlighting etc.)
    project_name: String,         // branding, loaded from /api/info
    project_description: String,
    cache: cache.Model,           // entity caches — studies/series/records/record_types/patients/users/record_type_stats
    viewers: List(ViewerInfo),    // configured viewers from /api/info at startup
    translate: fn(Key) -> String, // i18n lookup (from current locale)
    locale: Locale,               // current locale
  )
}
```

Pages read entities from `shared.cache.<entity>` (a `Dict(id, Entity)`). Do **not** store the same entity in your page `Model` — keep the page thin and let the cache be the source of truth. Copy into the page only for local edit buffers (e.g. form state before submit).

### `OutMsg` catalogue (the full set — keep in sync with `shared.gleam`)

UI feedback:
- `ShowSuccess(String)` — green toast, auto-dismiss 5s via `main.auto_dismiss_effects`
- `ShowError(String)` — red toast, auto-dismiss 5s
- `SetLoading(Bool)` — global spinner flag

Navigation:
- `Navigate(Route)` — pushes a new URL via `modem.push(router.route_to_path(route), None, None)`; triggers `OnRouteChange` → cleanup of current page → init of next page

Cache writes (use after a mutation succeeds — avoids an extra GET):
- `CacheRecord(Record)`, `CacheStudy(Study)`, `CachePatient(Patient)`, `CacheRecordType(RecordType)`, `CacheSeries(Series)`

Cache reloads (use when you need fresh data and don't have the entity in hand):
- `ReloadRecords`, `ReloadStudies`, `ReloadUsers`, `ReloadPatients`, `ReloadRecordTypes`, `ReloadRecordTypeStats`
- `ReloadPatient(id)`, `ReloadRecord(id)` — single-entity refetch

Modals:
- `OpenDeleteConfirm(resource, id)` — opens the generic confirm modal; `main.ConfirmModalAction` dispatches `PatientDetailMsg(Delete)` / `StudyDetailMsg(Delete)` depending on `resource` — extend that `case` arm when adding new resource types
- `OpenFailPrompt(record_id)` — opens the "fail record" textarea modal (handled entirely in `main`)

Auth:
- `SetUser(User)` — after login / user update
- `Logout` — starts the logout flow (API call + cookie clear + redirect)

Viewer:
- `StartPreload(viewer_url, study_uid)` — delegates to `preload.Start`; the preload module owns the progress modal

**Rule of thumb:** if a page wants to change global state, it adds an `OutMsg` variant. Never `import store` from a page.

## 3. Effects — Async + Dispatch

Lustre effects are `Effect(msg)`. The three patterns you'll use every day:

### Fire an HTTP call and dispatch a result

```gleam
fn load_patient_effect(patient_id: String) -> Effect(Msg) {
  use dispatch <- effect.from
  patients.get_patient(patient_id)
  |> promise.tap(fn(result) { dispatch(PatientLoaded(result)) })
  Nil
}
```

`effect.from` takes a callback with `dispatch` in scope. The returned `Nil` is important — `effect.from` expects the body to be `Nil`, and `promise.tap` already hooks the continuation for you.

### Batch multiple effects

```gleam
effect.batch([eff1, eff2, modem.push(path, None, None)])
```

### Delay / timer

```gleam
use dispatch <- effect.from
let _ = global.set_timeout(5000, fn() { dispatch(AutoClose) })
Nil
```

Use `plinth/javascript/global` for timers. If the timer can outlive the page, store the handle in your `Model` and cancel it in `cleanup`.

## 4. `LoadStatus` — Tri-State for Detail Pages

`utils/load_status.gleam`:

```gleam
pub type LoadStatus {
  Loading
  Loaded
  Failed(message: String)
}
```

**Why it exists:** previously detail pages did `case dict.get(shared.cache.patients, id)` inside the view. That cannot distinguish "cold cache, fetch in flight" from "fetch failed" — users saw an infinite spinner on error.

**Pattern:**

```gleam
pub type Model {
  Model(id: String, load_status: LoadStatus, ...)
}

pub fn init(id, _shared) {
  #(Model(id, load_status.Loading, ...), load_effect(id), [])
}

// In update:
Loaded(Ok(entity)) -> #(
  Model(..model, load_status: load_status.Loaded),
  effect.none(),
  [shared.CachePatient(entity)],  // feed the cache, don't duplicate in page Model
)
Loaded(Error(err)) -> #(
  Model(..model, load_status: load_status.Failed("Failed to load patient")),
  effect.none(),
  handle_error(err, "Failed to load patient"),  // see §6
)
RetryLoad -> #(
  Model(..model, load_status: load_status.Loading),
  load_effect(model.id),
  [],
)

// In view:
load_status.render(
  model.load_status,
  fn() { loading_spinner() },
  fn() { render_body(model, shared) },
  fn(msg) { error_with_retry(msg, RetryLoad) },
)
```

Always pair `Failed` with a `RetryLoad` message — the retry button is part of the contract.

## 5. Cache Access Rules

`shared.cache` is a `cache.Model` struct of `Dict(String, Entity)` fields. Inside a `view`:

```gleam
let studies =
  dict.values(shared.cache.studies)
  |> list.sort(fn(a, b) { string.compare(a.study_uid, b.study_uid) })
```

For detail pages, use `dict.get(shared.cache.patients, id)` inside the `on_loaded` callback of `load_status.render` — if the entry disappeared (e.g. cache reset), fall back to a transient spinner.

**Never** mutate `shared.cache` from a page. Emit `CacheX(entity)` / `ReloadX` `OutMsg` values instead.

Bulk loads go through `cache.LoadX` messages dispatched via `store.CacheMsg` — but you should **not** dispatch those from a page. Emit the corresponding `OutMsg` (`ReloadStudies` etc.) and let `main.apply_out_msgs` route it.

## 6. Error Handling Idiom

Canonical helper — copy-paste into each page that hits the API:

```gleam
fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]             // 401 — kill session
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}
```

Returning `[shared.Logout]` on `AuthError` is critical — it triggers the logout flow in `main` and redirects to `/login`. `main.handle_api_error` also has a safety net, but pages should still self-report.

Don't inspect `ServerError.code` / `ValidationError.errors` just to produce a generic toast — the fallback message is enough for most mutations. Detailed field-level errors belong in form-specific branches.

## 7. API Layer (`src/api/`)

One file per backend resource: `patients.gleam`, `records.gleam`, `studies.gleam`, `series.gleam`, `admin.gleam`, `dicom.gleam`, `dicomweb.gleam`, `slicer.gleam`, `users.gleam`, `auth.gleam`, `info.gleam`.

Shared plumbing (`http_client.gleam` — use only the `pub` helpers):
- `get(path)`, `post(path, body)`, `put(path, body)`, `patch(path, body)`, `delete(path)` — all return `Promise(Result(Dynamic, ApiError))`
- `post_multipart(path, form)` — multipart/form-data (used by login)
- `decode_response(data, decoder, error_msg) -> Result(a, ApiError)` — run a `dynamic/decode` decoder and map failure to `ParseError`
- `process_response` / `request_with_body` are public but called via the wrappers above — no reason to use them directly

**All** paths go through `config.base_path() <> "/api" <> path` internally — never hardcode `/api/...` in a caller; just pass `"/patients/123"` to `http_client.get`.

- `models.gleam` — Gleam record types mirroring backend Pydantic models
- `types.gleam` — `ApiError` union (`NetworkError | ParseError | AuthError | ServerError(code, msg) | ValidationError(fields)`)

Return type for every API function: `Promise(Result(T, ApiError))`. Decoders use `gleam/dynamic/decode` (see §9 for gotchas).

When adding an endpoint: add the function to the matching `api/<resource>.gleam`, write its decoder in `api/models.gleam`, then call it from the page's effect. If a new resource class appears, create a new `api/*.gleam` file — do not shoehorn unrelated endpoints into an existing one.

## 8. Routing — `config.base_path()`

The app is deployed behind a sub-path (`/liver_nir/`, `/lung_ct/`, ...). `config.base_path()` reads the prefix from a `<meta>` tag injected at serve time.

- **Always** build URLs via `router.route_to_path(router.SomeRoute(args))`. It prepends the prefix.
- **Never** construct anchor hrefs by string concatenation — you'll break sub-path deploys.
- `parse_route` strips the prefix before matching, so pattern-matching URL segments in `parse_route` uses the **clean** path (no prefix).
- E2E tests go through `PATH_PREFIX` from `deploy/vm/vm.conf`. If you change routing, update Playwright selectors too — see `.claude/rules/e2e-tests.md`.

### Changing a `Route` variant signature

Adding/removing a field on a `Route` variant (e.g. `Studies` → `Studies(filters: Dict(...))`) breaks every bare reference. **Before** the edit, grep all callers and treat the result as your file list:

```
rg -n 'router\.(Studies|Patients|...)\b' clarinet/frontend/src/
```

Typical hits beyond `router.gleam` itself: `main.gleam` (`init_page_for_route`), `components/layout.gleam` (`nav_link`), `pages/<area>/{detail,new}.gleam` (`shared.Navigate(router.X)`, `NavigateBack`), `pages/home.gleam` (stat-card routes). Plans listing only "files to edit" routinely miss these — verify yourself, don't trust the list.

**Inside `router.gleam` itself (the grep above doesn't catch these — they pattern-match on the constructor without a `router.` prefix):**

- `route_to_path` — URL builder; every variant must produce a path
- `parse_route` — URL → variant
- `get_route_title` — page title shown in `<title>` and the layout header
- `requires_auth` / `requires_admin_role` — guard predicates
- `section` — drives active-tab highlighting in the nav bar
- `route_to_query` — query-string serialization (only matters for variants with filters)

For a parameterised variant (`X(filters: Dict)`): match in **all six** functions above, plus `main.init_page_for_route`, plus every bare `router.X` reference in `components/layout.gleam` (`nav_link(route: ...)`) and pages doing `shared.Navigate(router.X)` / `router.route_to_path(router.X)`.

### Silent URL state sync — `utils/url.gleam`

When a Msg already mutates the page `Model` locally (e.g. column-sort click, filter toggle) and the URL change must **not** trigger `OnRouteChange` → `init_page_for_route` → API refetch, use `utils/url.replace_state(path, query)`. It calls `history.replaceState` via FFI without dispatching modem's `on_url_change`.

Use `modem.replace` / `modem.push` only when a full page re-init is the intended outcome (e.g. `Navigate` between sections). For SPA-internal state mirrored into the URL, prefer `url.replace_state`.

Reference: `pages/{studies,patients}/list.gleam` use it via `url.replace_route(route)`; sortable-table flow in `utils/table_sort.gleam` is the canonical example.

## 8.5. List Page Pattern (filters + sorting + URL/localStorage)

Pages that show a filterable, sortable list of cached entities follow one
template. Reference: `pages/records/list.gleam` (also `studies/list.gleam`,
`patients/list.gleam`). Reuse the building blocks below — do not reinvent
them in the page.

**Building blocks:**

- `utils/record_filters.gleam` — `apply_filters`, `clear_user_filters`,
  `keep_serializable`, dropdown option builders (`status_options`,
  `type_options`, `patient_options`). Two key constants:
  - `user_filter_keys` — what `clear_user_filters` removes (no sort).
  - `serializable_filter_keys` — superset that includes `sort` / `sort_dir`.
    Single source of truth for **both** URL parsing
    (`router.parse_filters_from_query`) and outgoing writes
    (`router.filters_to_query`, page `save_filters`).
- `utils/table_sort.gleam` — sortable column headers, URL-persisted sort.
- `utils/url.gleam` — `replace_state` / `replace_route` for silent URL sync.
- `utils/storage.gleam` — `save_dict` / `load_dict_sync` for localStorage.

**Model field:**

```gleam
pub type Model {
  Model(
    ...,
    active_filters: Dict(String, String),  // user filter keys + sort/sort_dir
  )
}
```

**Init flow:** read `filters` from URL → fall back to `localStorage` →
defensively pass through `record_filters.keep_serializable` so a stale
localStorage entry from a previous schema can't leak unknown keys into
the model.

**Update flow:** every filter/sort mutation calls `sync_filters_effect(filters)`
which `effect.batch`-es URL sync + localStorage save. Both writers must
go through `record_filters.keep_serializable` — `router.filters_to_query`
already does, the page-local `save_filters` must do the same. Otherwise
transient model fields can leak into persistent state.

**Adding a new filter dimension:**

1. Add the key to `record_filters.user_filter_keys` AND
   `record_filters.serializable_filter_keys` (same file, same change).
2. Extend `apply_filters` with the new branch.
3. Add a dropdown option builder if needed (`patient_options`-style).
4. Wire the dropdown in the page's `view`.

**Adding a new sort column:** see `utils/table_sort.gleam` — only the
per-page `record_comparator` changes.

## 9. Gleam / Decoder Gotchas

- `decode.optional_field(key, default, decoder)` — **default is the second argument**, not the last.
- `result.to_option()` does **NOT** exist — use `option.from_result()`.
- `decode.lazy()` does **NOT** exist — reference recursive decoders directly by name.
- `io.debug` is deprecated — use `io.println(string.inspect(value))` or the project's `utils/logger.gleam` (`logger.debug(tag, msg)`).
- Import types with the `type` keyword: `import api/models.{type Patient, type Record}`.
- `_name` is a discard pattern — cannot be read as a variable.
- Guards (`case x { y if ... }`) do not support function calls — use `<>` patterns or pre-compute the boolean.

## 10. Forms — formosh Integration

`formosh` is a private Gleam web-component library (`git@github.com:radionest/gleam_formosh.git`). It is registered once in `main.init` via `formosh_component.register()`.

Wrappers live in `src/components/forms/`:
- `base.gleam` — common field types, validators, submit helpers
- `patient_form.gleam`, `record_form.gleam` — domain-specific forms

When adding a form, **reuse** `components/forms/base.gleam` primitives rather than rolling raw `<form>` elements. The formosh component handles field-level validation, error display, and submit debouncing — agents accidentally re-implementing these is the biggest failure mode.

If you can't access the private repo, forms will break at build time; ask the user before proposing an alternative (rewriting to raw Lustre forms is a significant undertaking).

## 11. Logging

`utils/logger.gleam` exposes `debug/info/warn/error(tag, msg)` — each one wraps the matching `console.*` FFI from `plinth/javascript/console` and prepends `[tag]`. Use it; never `io.debug` in committed code.

```gleam
import utils/logger
logger.debug("router", "parsed route: " <> string.inspect(route))
logger.error("auth", "session check failed: " <> message)
```

Tags in current use: `router`, `auth`, `api`, `cache`, `preload`. Pick the closest or invent a new one.

## 11.5. Inserting Server-Sanitized HTML

Lustre escapes everything by default — `html.text(s)` always produces text, never markup. To render pre-sanitized HTML from the backend (e.g. `record.context_info_html`, produced by the markdown → bleach pipeline), set the DOM `innerHTML` **property** (not an attribute) on a wrapper element:

```gleam
import gleam/json
import lustre/attribute
import lustre/element       // for element.none()
import lustre/element/html

case record.context_info_html {
  Some(html_str) ->
    html.div(
      [
        attribute.class("context-info"),
        attribute.property("innerHTML", json.string(html_str)),
      ],
      [],   // children must be empty — innerHTML replaces them
    )
  None -> element.none()
}
```

Reference: `pages/records/execute.gleam` (`render_context_info`).

**Rules:**
- The HTML **must** already be sanitized on the backend. Never feed user-controlled raw HTML through `attribute.property("innerHTML", ...)` — it bypasses Lustre's escaping and is a stored XSS sink. The backend uses `nh3.clean(...)` with explicit tag/attribute/url-scheme allowlists (see `clarinet/utils/markdown.py`); mirror those allowlists whenever you add a new "render-HTML-from-server" surface.
- `attribute.property` (NOT `attribute.attribute`) — Lustre distinguishes DOM properties from HTML attributes. `innerHTML` is a property; the attribute form silently does nothing.
- Wrap the value in `json.string(...)` because `attribute.property` takes a `Json` value, not a raw `String`.
- Keep the children list empty (`[]`). Anything you put there is wiped by `innerHTML` on first render and reappears on the next, causing flicker.
- Pair with `Some/None` (or equivalent guard) — passing `json.string("")` still resets `innerHTML` and clobbers any default content.

The same pattern works for other DOM properties (`value` on inputs, `srcdoc` on iframes, etc.) — `attribute.property(name, json.<type>(value))`.

## 12. Common Pitfalls

- **Forgetting `delegate_page_update` wiring** — you add a new `PageMsg` variant in `store.Msg` but skip the dispatcher in `main.update`. Gleam will catch the missing case arm. Always add both the `store.Msg` variant AND the `main.update` delegation in the same change.
- **Forgetting `init_page_for_route` wiring** — the page compiles but never initializes; you'll see `NoPage` rendered (a blank loading placeholder). Always extend `init_page_for_route` when adding a route.
- **`page_init_eff` not mapped** — calling `page.init(args, shared)` directly in `main` without going through `init_page` drops `effect.map(_, store.YourPageMsg)` and messages from the effect get dispatched as the wrong variant. Always use the `init_page` helper.
- **Keeping an `Option(Entity)` in page `Model`** — duplicates the cache and goes stale. Use `load_status: LoadStatus` + `dict.get(shared.cache.X, id)` in view instead.
- **Mutating `shared.cache` by constructing a new `cache.Model`** — it has no effect; `shared` is rebuilt by `main.build_shared` every update. Emit `CacheX` / `ReloadX` instead.
- **Bypassing `router.route_to_path`** — hardcoded `href="/patients"` breaks under `/liver_nir/` deploy.
- **Not calling `cleanup` for timer pages** — route change doesn't fire JS garbage collection; timers keep dispatching into a stale page. Export `cleanup`, register it in `main.cleanup_current_page`.
- **Putting business rules in `view`** — derive a value in `update` and stash it in `Model`. `view` should be a pure projection.

## 13. Where to Look When Stuck

- **MVU wiring** — `src/main.gleam` (init_page_for_route, delegate_page_update, apply_out_msgs, view_content dispatch)
- **Global state shape** — `src/store.gleam` (Model, PageModel, Msg)
- **Shared contract** — `src/shared.gleam` (Shared, OutMsg — both types are ~50 lines total)
- **Cache shape** — `src/cache.gleam` (LoadX messages, put_X helpers, field names)
- **Canonical page examples**:
  - `src/pages/studies/list.gleam` — minimal page (no local state, pure projection over cache)
  - `src/pages/patients/detail.gleam` — full LoadStatus + mutations + PACS integration
  - `src/pages/records/execute.gleam` — timers, viewer handle, `cleanup` export
  - `src/pages/records/new.gleam` — form-heavy page using `components/forms/`

## 14. Gleam Toolchain

- **Binary**: if `gleam` is not in PATH, use `/home/linuxbrew/.linuxbrew/bin/gleam`
- **CWD**: always run gleam commands from `clarinet/frontend/` (where `gleam.toml` lives)
- **Quick check**: `make frontend-check` (type-checks without building)
- **Full build**: `make frontend-build`
- **Never** run `gleam check` from project root — it will fail with "gleam.toml not found"
