// Shared filter+sort state helpers for record-list pages.
// Both the standalone /records page and the Admin Dashboard's records
// section keep their filter/sort selection in a Dict(String, String)
// that is mirrored to the URL and to localStorage. The helpers here
// extract that lifecycle so each page only declares its own route
// constructor and storage key.

import api/models.{type Record}
import gleam/dict.{type Dict}
import gleam/int
import gleam/option
import gleam/order
import gleam/string
import lustre/effect.{type Effect}
import router.{type Route}
import utils/status
import utils/storage
import utils/url

/// Comparator for the columns shared by every record-list page.
/// Returns `Error(Nil)` for unknown columns so callers can fall back
/// to their page-specific comparators (e.g. "modality" or "user").
pub fn common_comparator(
  col: String,
) -> Result(fn(Record, Record) -> order.Order, Nil) {
  case col {
    "id" ->
      Ok(fn(a: Record, b: Record) {
        int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
      })
    "record_type" ->
      Ok(fn(a: Record, b: Record) {
        string.compare(a.record_type_name, b.record_type_name)
      })
    "status" ->
      Ok(fn(a: Record, b: Record) {
        string.compare(
          status.to_backend_string(a.status),
          status.to_backend_string(b.status),
        )
      })
    "patient" ->
      Ok(fn(a: Record, b: Record) {
        string.compare(a.patient_id, b.patient_id)
      })
    _ -> Error(Nil)
  }
}

/// Push the current filter dict to the URL (silently — no route change)
/// and persist it to localStorage under `storage_key`.
/// `route_for` builds the page's Route from the filter dict — pass the
/// route constructor directly (e.g. `router.Records`).
pub fn sync_filters_effect(
  filters: Dict(String, String),
  route_for: fn(Dict(String, String)) -> Route,
  storage_key: String,
) -> Effect(msg) {
  effect.batch([
    url.replace_route(route_for(filters)),
    storage.save_dict(storage.Local, storage_key, filters),
  ])
}

/// Resolve the initial filter dict for a page's `init`:
/// - URL filters non-empty → use them, save to localStorage.
/// - URL filters empty + localStorage non-empty → restore from storage,
///   reflect into the URL via `replace_state` so the address bar matches.
/// - Both empty → empty dict, no effect.
pub fn resolve_initial_filters(
  url_filters: Dict(String, String),
  storage_key: String,
  route_for: fn(Dict(String, String)) -> Route,
) -> #(Dict(String, String), Effect(msg)) {
  case dict.is_empty(url_filters) {
    False -> #(
      url_filters,
      storage.save_dict(storage.Local, storage_key, url_filters),
    )
    True -> {
      let saved = storage.load_dict_sync(storage.Local, storage_key)
      case dict.is_empty(saved) {
        True -> #(dict.new(), effect.none())
        False -> #(saved, url.replace_route(route_for(saved)))
      }
    }
  }
}
