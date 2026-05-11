---
description: Frontend API layer, routing under sub-path deploys, list-page pattern, formosh forms, server-sanitized HTML
paths:
  - "clarinet/frontend/src/**/*.gleam"
  - "clarinet/frontend/test/**/*.gleam"
---

# Frontend — API, Routing, Forms

API layer, routing under sub-path deploys, list-page filter/sort pattern, formosh forms integration, server-sanitized HTML injection.

Related rules: `frontend-page-contract.md` (page lifecycle, Shared/OutMsg, effects, cache, errors), `frontend-reference.md` (decoder gotchas, common pitfalls, toolchain).

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

Return type for every API function: `Promise(Result(T, ApiError))`. Decoders use `gleam/dynamic/decode` (see `frontend-reference.md` §9 for gotchas).

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

## 10. Forms — formosh Integration

`formosh` is a private Gleam web-component library (`git@github.com:radionest/gleam_formosh.git`). It is registered once in `main.init` via `formosh_component.register()`.

Wrappers live in `src/components/forms/`:
- `base.gleam` — common field types, validators, submit helpers
- `patient_form.gleam`, `record_form.gleam` — domain-specific forms

When adding a form, **reuse** `components/forms/base.gleam` primitives rather than rolling raw `<form>` elements. The formosh component handles field-level validation, error display, and submit debouncing — agents accidentally re-implementing these is the biggest failure mode.

If you can't access the private repo, forms will break at build time; ask the user before proposing an alternative (rewriting to raw Lustre forms is a significant undertaking).

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
