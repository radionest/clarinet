---
description: Frontend decoder gotchas, logging, common pitfalls, where to look when stuck, Gleam toolchain
paths:
  - "clarinet/frontend/src/**/*.gleam"
  - "clarinet/frontend/test/**/*.gleam"
---

# Frontend — Reference

Decoder gotchas, logging convention, common pitfalls, navigation reference, Gleam toolchain notes.

Related rules: `frontend-page-contract.md` (page contract, Shared/OutMsg), `frontend-routing-forms.md` (API/routing/forms).

## 9. Gleam / Decoder Gotchas

- `decode.optional_field(key, default, decoder)` — **default is the second argument**, not the last.
- `result.to_option()` does **NOT** exist — use `option.from_result()`.
- `decode.lazy()` does **NOT** exist — reference recursive decoders directly by name.
- `io.debug` is deprecated — use `io.println(string.inspect(value))` or the project's `utils/logger.gleam` (`logger.debug(tag, msg)`).
- Import types with the `type` keyword: `import api/models.{type Patient, type Record}`.
- `_name` is a discard pattern — cannot be read as a variable.
- Guards (`case x { y if ... }`) do not support function calls — use `<>` patterns or pre-compute the boolean.

## 11. Logging

`utils/logger.gleam` exposes `debug/info/warn/error(tag, msg)` — each one wraps the matching `console.*` FFI from `plinth/javascript/console` and prepends `[tag]`. Use it; never `io.debug` in committed code.

```gleam
import utils/logger
logger.debug("router", "parsed route: " <> string.inspect(route))
logger.error("auth", "session check failed: " <> message)
```

Tags in current use: `router`, `auth`, `api`, `cache`, `preload`. Pick the closest or invent a new one.

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
