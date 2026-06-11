// Build a `bucket.RecordsQuery` from the page's filter dict + sort/sort_dir.
//
// The filter dict (used by `pages/admin.gleam`, `pages/records/list.gleam`,
// and the URL/localStorage round-trip) stores user-facing keys: "status",
// "record_type", "patient", "user", plus "sort"/"sort_dir". This module
// converts those into the structured `RecordsQuery` that becomes the
// bucket key — same filters → same key → same cache entry, like
// TanStack Query's `queryKey`.

import cache/bucket.{
  type RecordsQuery, type SortOrder, IdAsc, IdDesc, ModalityAsc, ModalityDesc,
  PatientAsc, PatientDesc, RecordTypeAsc, RecordTypeDesc, RecordsQuery,
  StatusAsc, StatusDesc, UserAsc, UserDesc,
}
import gleam/dict.{type Dict}
import gleam/option.{None, Some}
import utils/record_filters

/// Convert the page's filter dict into a `RecordsQuery`.
///
/// `__unassigned__` for the user filter becomes `wo_user: Some(True)`; an
/// explicit user id also pins `wo_user: Some(False)` so the backend's
/// include_unassigned widening for regular users can't mix free records
/// into "assigned to X". Status and patient/record_type values are passed
/// through unchanged (they already match backend representations: status
/// uses the backend string, the others are FK values).
pub fn from_filters(filters: Dict(String, String)) -> RecordsQuery {
  let record_status = dict.get(filters, "status") |> option.from_result
  let record_type_name = dict.get(filters, "record_type") |> option.from_result
  let patient_id = dict.get(filters, "patient") |> option.from_result
  let raw_user = dict.get(filters, "user") |> option.from_result
  let #(user_id, wo_user) = case raw_user {
    Some(v) ->
      case v == record_filters.unassigned_user_value {
        True -> #(None, Some(True))
        False -> #(Some(v), Some(False))
      }
    None -> #(None, None)
  }
  RecordsQuery(
    patient_id: patient_id,
    study_uid: None,
    record_type_name: record_type_name,
    record_status: record_status,
    user_id: user_id,
    wo_user: wo_user,
    sort: parse_sort_from_filters(filters),
  )
}

/// Read `sort`/`sort_dir` keys from a filter dict and convert them into the
/// strongly-typed `SortOrder`. Missing or unknown keys fall back to
/// `IdAsc` — matches the `default_sort_col = "id"` baseline that
/// `table_sort.read_sort` uses in admin / records/list, so the column the
/// UI highlights with an arrow agrees with the order the backend actually
/// returns. (Defaulting to backend's `changed_at_desc` here would silently
/// disagree with the UI arrow when `table_sort.write_sort` drops the
/// sort keys as "default ASC on the default column".)
pub fn parse_sort_from_filters(filters: Dict(String, String)) -> SortOrder {
  let col = dict.get(filters, "sort") |> option.from_result
  let dir = dict.get(filters, "sort_dir") |> option.from_result
  parse_sort(col, dir)
}

pub fn parse_sort(
  col: option.Option(String),
  dir: option.Option(String),
) -> SortOrder {
  let ascending = case dir {
    Some("desc") -> False
    _ -> True
  }
  case col {
    Some("id") ->
      case ascending {
        True -> IdAsc
        False -> IdDesc
      }
    Some("record_type") ->
      case ascending {
        True -> RecordTypeAsc
        False -> RecordTypeDesc
      }
    Some("status") ->
      case ascending {
        True -> StatusAsc
        False -> StatusDesc
      }
    Some("patient") ->
      case ascending {
        True -> PatientAsc
        False -> PatientDesc
      }
    Some("user") ->
      case ascending {
        True -> UserAsc
        False -> UserDesc
      }
    Some("modality") ->
      case ascending {
        True -> ModalityAsc
        False -> ModalityDesc
      }
    // Unknown / missing column → always IdAsc, ignoring sort_dir. A stale
    // URL like `?sort=removed_col&sort_dir=desc` should not silently flip
    // to IdDesc just because the direction key survived.
    _ -> IdAsc
  }
}

/// Layer a user_id scope on top of an existing query. Used by the
/// `/records` page to restrict non-admins to their own records — the old
/// `RecordsMine(uid)` bucket variant. `wo_user` is reset to None (no
/// constraint) so the server-side default for regular users — own plus
/// unassigned records — stays intact.
pub fn with_user_scope(query: RecordsQuery, user_id: String) -> RecordsQuery {
  RecordsQuery(..query, user_id: Some(user_id), wo_user: None)
}

/// Scope a filter-derived query for a regular (non-admin) user on the
/// /records page. An explicit `user` filter is honoured only within the
/// caller's own scope:
/// - their own id — strict "assigned to me" (`from_filters` already
///   pinned `wo_user: Some(False)`);
/// - `__unassigned__` — free records; the caller's id is attached so the
///   backend's unique-per-user violation filter (which requires user_id)
///   hides free records the caller can't actually claim;
/// - any other id, or no user filter — clobbered by `with_user_scope`.
pub fn scope_for_user(
  query: RecordsQuery,
  filters: Dict(String, String),
  user_id: String,
) -> RecordsQuery {
  case dict.get(filters, "user") {
    Ok(v) ->
      case v == record_filters.unassigned_user_value, v == user_id {
        True, _ -> RecordsQuery(..query, user_id: Some(user_id))
        False, True -> query
        False, False -> with_user_scope(query, user_id)
      }
    Error(_) -> with_user_scope(query, user_id)
  }
}

/// Pin an existing query to a single patient. Used by the patient detail
/// page so its records list reuses the server-side filter/sort machinery
/// while staying scoped to one patient (mirrors `with_user_scope`).
pub fn with_patient_scope(
  query: RecordsQuery,
  patient_id: String,
) -> RecordsQuery {
  RecordsQuery(..query, patient_id: Some(patient_id))
}
