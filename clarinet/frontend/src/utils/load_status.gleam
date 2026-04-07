// Page-level load status for detail pages that pull data via shared cache.
//
// Previously, detail pages used `case dict.get(cache, id)` in their view,
// which could not distinguish a cold loading state from a failed API call —
// a failed load would keep rendering the spinner forever while the only hint
// was a global toast. `LoadStatus` makes the tri-state explicit so pages can
// render a retry UI on failure.

import api/types.{type ApiError}
import lustre/element.{type Element}

pub type LoadStatus {
  Loading
  Loaded
  Failed(message: String)
}

/// Collapses an API `Result` into a `LoadStatus`. The detailed error usually
/// flows into a global toast via `handle_error`; this helper only needs to
/// remember the fact of failure plus a short fallback message for the retry
/// view.
pub fn from_result(result: Result(a, ApiError), fallback_msg: String) -> LoadStatus {
  case result {
    Ok(_) -> Loaded
    Error(_) -> Failed(fallback_msg)
  }
}

/// Dispatches to one of the three view callbacks based on the current status.
/// Pages still do their own cache lookup inside `on_loaded` so they can fall
/// back to a transient spinner if the cache entry disappeared.
pub fn render(
  status: LoadStatus,
  on_loading: fn() -> Element(msg),
  on_loaded: fn() -> Element(msg),
  on_failed: fn(String) -> Element(msg),
) -> Element(msg) {
  case status {
    Loading -> on_loading()
    Loaded -> on_loaded()
    Failed(msg) -> on_failed(msg)
  }
}
