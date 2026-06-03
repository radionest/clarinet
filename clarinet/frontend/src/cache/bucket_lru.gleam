import cache/bucket.{type Bucket}
import gleam/dict.{type Dict}
import gleam/option.{type Option, None, Some}
import gleam/order
import gleam/string

/// Upper bound on distinct entries in `cache.Model.record_buckets`.
///
/// Each `(filters × sort)` combination on `/records` produces a
/// separate bucket. With ~5 filter dimensions × 13 sort orders the
/// theoretical max is in the dozens, but normal usage (a couple of
/// filters + a few sort experiments per page mount) stays well under
/// 20. Bursts beyond that fall back to the eviction policy in
/// `insert_bounded`. Frontend memory concern — not a deployment knob,
/// tune here.
pub const max_record_buckets: Int = 20

/// Insert `b` under `topic`, then evict the oldest evictable entry if
/// the dictionary now exceeds `max_record_buckets`.
///
/// "LRU" here means **least-recently-loaded** — eviction priority is
/// the `loaded_at_ms` carried by `Live` / `Stale`, NOT a view-access
/// timestamp. A bucket the user is actively viewing can still be
/// evicted if it loaded earlier than its neighbours; this is acceptable
/// because each filter switch produces a fresh `BucketLoaded` event
/// that refreshes the timestamp.
///
/// Eviction priorities (smallest evicts first):
/// - `None` (skipped) — the just-inserted `topic`, or any `Loading` /
///   `LoadingMore` bucket: dropping an in-flight response would either
///   stall the UI or trigger a wasteful re-fetch.
/// - `Some(0)` — `Cold` (no data at all).
/// - `Some(1)` — `Failed(_)` (no fresh data, but may hold the user's
///   last-good items via stale-while-revalidate).
/// - `Some(loaded_at_ms)` — `Live` / `Stale`, ranked oldest-first.
///
/// Ties broken by lexicographic topic order — deterministic across the
/// JS and Erlang targets, which disagree on `dict.fold` order.
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
    Some(#(best_key, best_at)) ->
      case prefers(at, key, best_at, best_key) {
        True -> Some(#(key, at))
        False -> acc
      }
  }
}

/// True iff candidate `(at_a, key_a)` should evict ahead of incumbent
/// `(at_b, key_b)`. Ordered primarily by `at` (smaller wins), with
/// lexicographic `key` as the tie-break for fold-order-independent
/// deterministic eviction.
fn prefers(at_a: Int, key_a: String, at_b: Int, key_b: String) -> Bool {
  case at_a == at_b {
    True -> string.compare(key_a, key_b) == order.Lt
    False -> at_a < at_b
  }
}

/// Eviction weight. `None` = in-flight, never evict. `Some(at)` —
/// smaller evicts first. `Cold` = 0, `Failed(_)` = 1 (slightly above
/// Cold to protect stale-while-revalidate items), `Live` / `Stale` use
/// their `loaded_at_ms`.
fn evictable_priority(b: Bucket) -> Option(Int) {
  case b.status {
    bucket.Loading | bucket.LoadingMore(_) -> None
    bucket.Live(at) | bucket.Stale(at) -> Some(at)
    bucket.Failed(_) -> Some(1)
    bucket.Cold -> Some(0)
  }
}
