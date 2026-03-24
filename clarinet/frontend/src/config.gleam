/// Application configuration derived from the HTML page.
///
/// Reads the `<base href>` tag injected by the backend to determine
/// the sub-path prefix (e.g. "/liver_nir" or "" for root deployment).
import gleam/result
import gleam/string
import plinth/browser/document
import plinth/browser/element

/// Base path prefix for the application.
/// Returns "" for root deployment, "/liver_nir" for sub-path deployment.
pub fn base_path() -> String {
  let path = {
    use el <- result.try(document.query_selector("base"))
    element.get_attribute(el, "href")
  }
  // Strip trailing slash: "/liver_nir/" → "/liver_nir", "/" → ""
  path
  |> result.unwrap("/")
  |> string.drop_end(up_to: 1)
}
