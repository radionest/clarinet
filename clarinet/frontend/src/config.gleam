/// Application configuration derived from the HTML page.
///
/// Reads the `<base href>` tag injected by the backend to determine
/// the sub-path prefix (e.g. "/liver_nir" or "" for root deployment).
/// The value is computed once on first access via `lazy_const`.
import gleam/result
import gleam/string
import lazy_const
import plinth/browser/document
import plinth/browser/element

/// Base path prefix for the application.
/// Returns "" for root deployment, "/liver_nir" for sub-path deployment.
/// Cached after first call — the `<base href>` never changes at runtime.
pub fn base_path() -> String {
  use <- lazy_const.new(lazy_const.defined_in(base_path))
  let path = {
    use el <- result.try(document.query_selector("base"))
    element.get_attribute(el, "href")
  }
  // Strip trailing slash: "/liver_nir/" → "/liver_nir", "/" → ""
  let p = result.unwrap(path, "/")
  case string.ends_with(p, "/") {
    True -> string.drop_end(p, up_to: 1)
    False -> p
  }
}
