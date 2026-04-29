// Silent URL synchronisation — updates window.location without triggering
// modem's on_url_change handler. Suitable when the page Model has already
// been mutated locally (e.g. column-sort click) and a full route re-init
// would only cause a redundant API reload.
import gleam/option.{type Option, Some}
import lustre/effect.{type Effect}
import router.{type Route}

@external(javascript, "./url.ffi.mjs", "replace_state")
fn do_replace_state(_path: String) -> Nil {
  Nil
}

pub fn replace_state(path: String, query: Option(String)) -> Effect(msg) {
  use _ <- effect.from
  let full = case query {
    Some(q) -> path <> "?" <> q
    _ -> path
  }
  do_replace_state(full)
  Nil
}

/// Convenience wrapper: serialise a Route via `router.route_to_path` /
/// `router.route_to_query` and call `replace_state`.
pub fn replace_route(route: Route) -> Effect(msg) {
  replace_state(router.route_to_path(route), router.route_to_query(route))
}
