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
/// `__unassigned__` for the user filter becomes `wo_user: True`. Status and
/// patient/record_type values are passed through unchanged (they already
/// match backend representations: status uses the backend string, the
/// others are FK values).
pub fn from_filters(filters: Dict(String, String)) -> RecordsQuery {
  let record_status = dict.get(filters, "status") |> option.from_result
  let record_type_name = dict.get(filters, "record_type") |> option.from_result
  let patient_id = dict.get(filters, "patient") |> option.from_result
  let raw_user = dict.get(filters, "user") |> option.from_result
  let #(user_id, wo_user) = case raw_user {
    Some(v) ->
      case v == record_filters.unassigned_user_value {
        True -> #(None, True)
        False -> #(Some(v), False)
      }
    None -> #(None, False)
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
/// `RecordsMine(uid)` bucket variant.
pub fn with_user_scope(query: RecordsQuery, user_id: String) -> RecordsQuery {
  RecordsQuery(..query, user_id: Some(user_id), wo_user: False)
}
