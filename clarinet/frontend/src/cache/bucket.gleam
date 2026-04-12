import api/models.{type Record}
import gleam/option.{type Option, None}

pub const ttl_ms: Int = 60_000

pub type BucketKey {
  RecordsAll
  RecordsMine(user_id: String)
  RecordsByPatient(patient_id: String)
  RecordsByStudy(study_uid: String)
  RecordsByRecordType(name: String)
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

pub fn key_to_topic(key: BucketKey) -> String {
  case key {
    RecordsAll -> "records:all"
    RecordsMine(uid) -> "records:mine:" <> uid
    RecordsByPatient(pid) -> "records:patient:" <> pid
    RecordsByStudy(suid) -> "records:study:" <> suid
    RecordsByRecordType(name) -> "records:type:" <> name
  }
}
