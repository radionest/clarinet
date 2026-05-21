import api/models.{type Record}
import gleam/option.{type Option, None, Some}

pub const ttl_ms: Int = 60_000

/// Server-side sort order for /records/find. Mirrors backend
/// `clarinet.utils.pagination.SortOrder`. The frontend builds this from
/// user-driven column clicks; the server actually performs the ordering
/// and keyset pagination, so it is part of the bucket key.
pub type SortOrder {
  ChangedAtDesc
  IdAsc
  IdDesc
  RecordTypeAsc
  RecordTypeDesc
  StatusAsc
  StatusDesc
  PatientAsc
  PatientDesc
  UserAsc
  UserDesc
  ModalityAsc
  ModalityDesc
}

/// Parameters for a /records/find query. Each distinct combination is a
/// distinct bucket key (and therefore a distinct cache entry, like a
/// TanStack Query `queryKey`).
pub type RecordsQuery {
  RecordsQuery(
    patient_id: Option(String),
    study_uid: Option(String),
    record_type_name: Option(String),
    record_status: Option(String),
    user_id: Option(String),
    wo_user: Bool,
    sort: SortOrder,
  )
}

pub type BucketKey {
  Records(query: RecordsQuery)
}

pub type BucketStatus {
  Cold
  Loading
  Live(loaded_at_ms: Int)
  Stale(loaded_at_ms: Int)
  Failed(err: String)
  LoadingMore(loaded_at_ms: Int)
}

pub type Bucket {
  Bucket(
    key: BucketKey,
    status: BucketStatus,
    items: List(Record),
    next_cursor: Option(String),
  )
}

pub fn new(key: BucketKey) -> Bucket {
  Bucket(key: key, status: Cold, items: [], next_cursor: None)
}

pub fn is_fresh(status: BucketStatus, now_ms: Int) -> Bool {
  case status {
    Live(loaded_at) | LoadingMore(loaded_at) -> now_ms - loaded_at < ttl_ms
    _ -> False
  }
}

pub fn mark_stale(bucket: Bucket) -> Bucket {
  case bucket.status {
    Live(at) -> Bucket(..bucket, status: Stale(at))
    LoadingMore(at) -> Bucket(..bucket, status: Stale(at))
    _ -> bucket
  }
}

pub fn default_query() -> RecordsQuery {
  RecordsQuery(
    patient_id: None,
    study_uid: None,
    record_type_name: None,
    record_status: None,
    user_id: None,
    wo_user: False,
    sort: ChangedAtDesc,
  )
}

pub fn query_with_patient(patient_id: String) -> RecordsQuery {
  RecordsQuery(..default_query(), patient_id: Some(patient_id))
}

pub fn query_with_study(study_uid: String) -> RecordsQuery {
  RecordsQuery(..default_query(), study_uid: Some(study_uid))
}

pub fn query_with_record_type(name: String) -> RecordsQuery {
  RecordsQuery(..default_query(), record_type_name: Some(name))
}

pub fn sort_to_backend_string(sort: SortOrder) -> String {
  case sort {
    ChangedAtDesc -> "changed_at_desc"
    IdAsc -> "id_asc"
    IdDesc -> "id_desc"
    RecordTypeAsc -> "record_type_asc"
    RecordTypeDesc -> "record_type_desc"
    StatusAsc -> "status_asc"
    StatusDesc -> "status_desc"
    PatientAsc -> "patient_asc"
    PatientDesc -> "patient_desc"
    UserAsc -> "user_asc"
    UserDesc -> "user_desc"
    ModalityAsc -> "modality_asc"
    ModalityDesc -> "modality_desc"
  }
}

/// Stable string representation of a bucket key. Used as the dict key in
/// `cache.Model.record_buckets`, so the encoding must be deterministic for
/// a given `RecordsQuery` value — same filters produce the same topic.
pub fn key_to_topic(key: BucketKey) -> String {
  let Records(q) = key
  let parts = [
    "records",
    "sort=" <> sort_to_backend_string(q.sort),
    optional_part("patient", q.patient_id),
    optional_part("study", q.study_uid),
    optional_part("type", q.record_type_name),
    optional_part("status", q.record_status),
    optional_part("user", q.user_id),
    case q.wo_user {
      True -> "wo_user=1"
      False -> ""
    },
  ]
  join_non_empty(parts, "|")
}

fn optional_part(label: String, value: Option(String)) -> String {
  case value {
    Some(v) -> label <> "=" <> v
    None -> ""
  }
}

fn join_non_empty(parts: List(String), sep: String) -> String {
  case parts {
    [] -> ""
    [first, ..rest] -> do_join(rest, first, sep)
  }
}

fn do_join(parts: List(String), acc: String, sep: String) -> String {
  case parts {
    [] -> acc
    [p, ..rest] ->
      case p {
        "" -> do_join(rest, acc, sep)
        _ -> do_join(rest, acc <> sep <> p, sep)
      }
  }
}
