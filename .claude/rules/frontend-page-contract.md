---
description: Frontend page module contract — MVU lifecycle, Shared/OutMsg, effects, LoadStatus, cache, errors
paths:
  - "clarinet/frontend/src/**/*.gleam"
  - "clarinet/frontend/test/**/*.gleam"
---

# Frontend MVU — Page Contract

Page lifecycle, shared state, effects, and error handling. Auto-loaded when editing Gleam files. High-level overview is in `clarinet/frontend/CLAUDE.md`; **that** file + this one are the two sources of truth for the page module contract.

Related rules: `frontend-routing-forms.md` (API layer, routing, forms, server HTML), `frontend-reference.md` (decoder gotchas, logging, pitfalls, toolchain).

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
