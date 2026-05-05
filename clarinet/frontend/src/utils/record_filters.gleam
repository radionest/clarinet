// Shared filter helpers for Record lists
import api/models.{type Record, type User}
import clarinet_frontend/i18n.{type Key}
import gleam/dict.{type Dict}
import gleam/list
import gleam/option.{None, Some}
import gleam/string
import utils/status

// User-controlled filter keys for the records list. Subset of
// `router.known_filter_keys` (which also accepts sort / sort_dir).
// When adding a new filter, update both lists.
pub const user_filter_keys = ["status", "record_type", "patient", "user"]

/// Special "user" filter value matching records with no assigned user.
/// Wrapped in double underscores to make it visually distinct from real
/// User.id UUIDs in URLs and avoid accidental collisions.
pub const unassigned_user_value = "__unassigned__"

/// Strip the user-controlled filter keys from `filters`, leaving any
/// other keys (notably `"sort"` / `"sort_dir"`) intact. Used by
/// "Clear filters" actions that should reset filtering without
/// touching the sort selection.
pub fn clear_user_filters(filters: Dict(String, String)) -> Dict(String, String) {
  list.fold(user_filter_keys, filters, fn(acc, key) { dict.delete(acc, key) })
}

/// True if `filters` contains at least one user-controlled key. Drives the
/// "Clear filters" button visibility — we ignore `sort`/`sort_dir` because
/// sorting is independent and shouldn't make a filter-reset button appear.
pub fn has_user_filters(filters: Dict(String, String)) -> Bool {
  list.any(user_filter_keys, fn(key) { dict.has_key(filters, key) })
}

/// Filter records by an active filter dict.
/// Recognised keys: `"status"`, `"record_type"`, `"patient"`, `"user"`.
/// For `"user"`, the special value `unassigned_user_value` matches records
/// with no assigned user. Missing keys mean "no filter on that dimension".
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

    let user_ok = case dict.get(filters, "user") {
      Ok(user_filter) ->
        case user_filter == unassigned_user_value {
          True -> record.user_id == None
          False -> record.user_id == Some(user_filter)
        }
      Error(_) -> True
    }

    status_ok && type_ok && patient_ok && user_ok
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

/// Build dropdown options for the assigned-user filter.
/// Includes only users actually referenced by `records` so the dropdown
/// stays scoped to what the table can show. The "unassigned" entry is
/// included whenever any record has no assigned user.
pub fn user_options(
  records: List(Record),
  users: Dict(String, User),
  translate: fn(Key) -> String,
) -> List(#(String, String)) {
  let referenced_ids =
    list.filter_map(records, fn(r) {
      case r.user_id {
        Some(uid) -> Ok(uid)
        None -> Error(Nil)
      }
    })
    |> list.unique()

  let user_entries =
    list.map(referenced_ids, fn(uid) {
      let label = case dict.get(users, uid) {
        Ok(user) -> user.email
        Error(_) -> uid
      }
      #(uid, label)
    })
    |> list.sort(fn(a, b) { string.compare(a.1, b.1) })

  let has_unassigned =
    list.any(records, fn(r) {
      case r.user_id {
        None -> True
        Some(_) -> False
      }
    })

  let unassigned_entry = case has_unassigned {
    True -> [#(unassigned_user_value, translate(i18n.FormNoUserUnassigned))]
    False -> []
  }

  [#("", translate(i18n.FilterAllUsers)), ..unassigned_entry]
  |> list.append(user_entries)
}
