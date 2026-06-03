import cache/bucket.{type Bucket}
import gleam/dict.{type Dict}
import gleam/option.{type Option, None, Some}

/// Upper bound on distinct entries in `cache.Model.record_buckets`.
///
/// Each `(filters × sort)` combination produces a separate bucket, so a
/// user rapidly clicking sortable headers under a heavy filter set can
/// transiently inflate the dict beyond the 60 s TTL + stale-drop GC
/// horizon. 20 covers typical filter+sort variety per page mount;
/// anything beyond that falls back to the LRU policy in
/// `insert_bounded`.
pub const max_record_buckets: Int = 20

/// Insert `b` under `topic`, then evict the entry with the oldest
/// `loaded_at_ms` if the dictionary now exceeds `max_record_buckets`.
///
/// The just-inserted `topic` is excluded from eviction candidates, so a
/// burst of filter switches never drops the bucket the user is actively
/// waiting on. Buckets without a loaded timestamp (Cold / Loading /
/// Failed) sort as 0 and are evicted before any Live / Stale /
/// LoadingMore entry.
pub fn insert_bounded(
  buckets: Dict(String, Bucket),
  topic: String,
  b: Bucket,
) -> Dict(String, Bucket) {
  let with_new = dict.insert(buckets, topic, b)
  case dict.size(with_new) > max_record_buckets {
    False -> with_new
    True ->
      case oldest_evictable_topic(with_new, topic) {
        Some(victim) -> dict.delete(with_new, victim)
        None -> with_new
      }
  }
}

fn oldest_evictable_topic(
  buckets: Dict(String, Bucket),
  protected: String,
) -> Option(String) {
  let folded = {
    use acc, key, b <- dict.fold(buckets, None)
    case key == protected {
      True -> acc
      False -> consider_for_oldest(acc, key, bucket_loaded_at(b))
    }
  }
  case folded {
    Some(#(k, _)) -> Some(k)
    None -> None
  }
}

fn consider_for_oldest(
  acc: Option(#(String, Int)),
  key: String,
  at: Int,
) -> Option(#(String, Int)) {
  case acc {
    None -> Some(#(key, at))
    Some(#(_, best_at)) ->
      case at < best_at {
        True -> Some(#(key, at))
        False -> acc
      }
  }
}

fn bucket_loaded_at(b: Bucket) -> Int {
  case b.status {
    bucket.Live(at) | bucket.Stale(at) | bucket.LoadingMore(at) -> at
    bucket.Cold | bucket.Loading | bucket.Failed(_) -> 0
  }
}
