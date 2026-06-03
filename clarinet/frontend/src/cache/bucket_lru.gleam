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

/// Insert `b` under `topic`, then evict the oldest evictable entry if
/// the dictionary now exceeds `max_record_buckets`.
///
/// Eviction rules:
/// - The just-inserted `topic` is protected — a burst of filter
///   switches never drops the bucket the user is actively waiting on.
/// - `Loading` / `LoadingMore` buckets are protected too: their
///   responses are in flight; dropping them would either stall the UI
///   (no `BucketLoaded` would update an evicted topic) or trigger a
///   wasteful re-fetch the next time the user revisits the same filter.
/// - Remaining candidates are ranked by `loaded_at_ms`; `Cold` and
///   `Failed(_)` rank as 0 and evict before any timestamped entry.
///
/// If every non-protected entry is in flight, the cap is temporarily
/// exceeded — preferable to silently dropping an active request.
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
      False ->
        case evictable_priority(b) {
          None -> acc
          Some(at) -> consider_for_oldest(acc, key, at)
        }
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

/// Eviction weight. `None` = in-flight, never evict. `Some(at)` —
/// candidates ranked by ascending `at`; Cold / Failed sort as 0, Live /
/// Stale by their `loaded_at_ms`.
fn evictable_priority(b: Bucket) -> Option(Int) {
  case b.status {
    bucket.Loading | bucket.LoadingMore(_) -> None
    bucket.Live(at) | bucket.Stale(at) -> Some(at)
    bucket.Cold | bucket.Failed(_) -> Some(0)
  }
}
