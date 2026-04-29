// Shared filter helpers for Record lists
import api/models.{type Record}
import clarinet_frontend/i18n.{type Key}
import gleam/dict.{type Dict}
import gleam/list
import gleam/string
import utils/status

const user_filter_keys = ["status", "record_type", "patient"]

/// Strip the user-controlled filter keys from `filters`, leaving any
/// other keys (notably `"sort"` / `"sort_dir"`) intact. Used by
/// "Clear filters" actions that should reset filtering without
/// touching the sort selection.
pub fn clear_user_filters(filters: Dict(String, String)) -> Dict(String, String) {
  list.fold(user_filter_keys, filters, fn(acc, key) { dict.delete(acc, key) })
}

/// Filter records by an active filter dict (keys: "status", "record_type", "patient").
/// Missing keys mean "no filter on that dimension".
pub fn apply_filters(
  records: List(Record),
  filters: Dict(String, String),
) -> List(Record) {
  list.filter(records, fn(record) {
    let status_ok = case dict.get(filters, "status") {
      Ok(status_filter) ->
        status.to_backend_string(record.status) == status_filter
      Error(_) -> True
    }

    let type_ok = case dict.get(filters, "record_type") {
      Ok(type_filter) -> record.record_type_name == type_filter
      Error(_) -> True
    }

    let patient_ok = case dict.get(filters, "patient") {
      Ok(patient_filter) -> record.patient_id == patient_filter
      Error(_) -> True
    }

    status_ok && type_ok && patient_ok
  })
}

/// Static dropdown options for the status filter.
pub fn status_options(translate: fn(Key) -> String) -> List(#(String, String)) {
  [
    #("", translate(i18n.FilterAllStatuses)),
    #("blocked", translate(i18n.StatusBlocked)),
    #("pending", translate(i18n.StatusPending)),
    #("inwork", translate(i18n.StatusInProgress)),
    #("finished", translate(i18n.StatusCompleted)),
    #("failed", translate(i18n.StatusFailed)),
    #("paused", translate(i18n.StatusPaused)),
  ]
}

/// Build dropdown options for the record type filter from the given records.
pub fn type_options(
  records: List(Record),
  translate: fn(Key) -> String,
) -> List(#(String, String)) {
  let types =
    list.map(records, fn(r) { r.record_type_name })
    |> list.unique()
    |> list.sort(fn(a, b) { string.compare(a, b) })
  [#("", translate(i18n.FilterAllTypes)), ..list.map(types, fn(t) { #(t, t) })]
}

/// Build dropdown options for the patient filter from the given records.
pub fn patient_options(
  records: List(Record),
  translate: fn(Key) -> String,
) -> List(#(String, String)) {
  let patients =
    list.map(records, fn(r) { r.patient_id })
    |> list.unique()
    |> list.sort(fn(a, b) { string.compare(a, b) })
  [#("", translate(i18n.FilterAllPatients)), ..list.map(patients, fn(p) { #(p, p) })]
}
