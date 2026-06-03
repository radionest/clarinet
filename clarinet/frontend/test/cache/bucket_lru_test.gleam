// Unit tests for cache/bucket_lru — record_buckets LRU bound (issue #301).
import cache/bucket.{
  type Bucket, Bucket, Cold, Failed, Live, Loading, LoadingMore, Records, Stale,
}
import cache/bucket_lru
import gleam/dict
import gleam/int
import gleam/option.{None}
import gleeunit/should

fn make_bucket(status: bucket.BucketStatus) -> Bucket {
  Bucket(
    key: Records(bucket.default_query()),
    status: status,
    items: [],
    next_cursor: None,
  )
}

fn topic(i: Int) -> String {
  "topic-" <> int.to_string(i)
}

// --- under cap: behaves like dict.insert ---

pub fn insert_under_cap_keeps_all_entries_test() {
  let buckets = {
    use acc, i <- int.range(from: 0, to: 5, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Live(i * 1000)))
  }
  dict.size(buckets) |> should.equal(5)
}

pub fn replace_existing_key_does_not_grow_test() {
  let initial =
    dict.new() |> dict.insert(topic(1), make_bucket(Live(100)))
  let result = bucket_lru.insert_bounded(initial, topic(1), make_bucket(Live(200)))
  dict.size(result) |> should.equal(1)
}

// --- at cap: oldest evicted ---

pub fn evicts_oldest_when_over_cap_test() {
  // Seed 20 entries with monotonically increasing loaded_at.
  let buckets = {
    use acc, i <- int.range(from: 0, to: 20, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Live(1000 + i)))
  }
  dict.size(buckets) |> should.equal(20)

  // 21st insert evicts topic-0 (loaded_at=1000, oldest).
  let after = bucket_lru.insert_bounded(buckets, "fresh", make_bucket(Live(99_999)))
  dict.size(after) |> should.equal(20)
  dict.has_key(after, topic(0)) |> should.equal(False)
  dict.has_key(after, "fresh") |> should.equal(True)
}

pub fn protects_just_inserted_topic_test() {
  // Seed 20 entries with NEWER loaded_at values.
  let buckets = {
    use acc, i <- int.range(from: 0, to: 20, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Live(10_000 + i)))
  }

  // 21st insert carries the smallest loaded_at but still survives —
  // eviction skips the topic we just inserted.
  let after = bucket_lru.insert_bounded(buckets, "fresh", make_bucket(Live(0)))
  dict.has_key(after, "fresh") |> should.equal(True)
  // topic-0 (loaded_at=10_000) is the oldest of the remaining 20 and is dropped.
  dict.has_key(after, topic(0)) |> should.equal(False)
}

// --- in-flight buckets (Loading / LoadingMore) are protected ---

pub fn does_not_evict_loading_buckets_test() {
  // Seed: 19 Live buckets with timestamps 1..19 + a Loading bucket.
  let seeded = {
    use acc, i <- int.range(from: 1, to: 20, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Live(i)))
  }
  let with_loading =
    bucket_lru.insert_bounded(seeded, "loading", make_bucket(Loading))
  dict.size(with_loading) |> should.equal(20)

  // 21st insert evicts the oldest Live (topic-1), NOT the Loading
  // bucket — dropping an in-flight request would either stall the UI
  // or force a redundant re-fetch.
  let after =
    bucket_lru.insert_bounded(with_loading, "fresh", make_bucket(Live(99)))
  dict.size(after) |> should.equal(20)
  dict.has_key(after, "loading") |> should.equal(True)
  dict.has_key(after, topic(1)) |> should.equal(False)
  dict.has_key(after, topic(2)) |> should.equal(True)
}

pub fn does_not_evict_loading_more_buckets_test() {
  // 19 Live entries + 1 LoadingMore (in-flight after a prior load with
  // a small loaded_at_ms — would normally be a strong eviction
  // candidate but is in flight and so protected).
  let seeded = {
    use acc, i <- int.range(from: 1, to: 20, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Live(10_000 + i)))
  }
  let with_loading_more =
    bucket_lru.insert_bounded(
      seeded,
      "loading-more",
      make_bucket(LoadingMore(50)),
    )
  dict.size(with_loading_more) |> should.equal(20)

  let after =
    bucket_lru.insert_bounded(
      with_loading_more,
      "fresh",
      make_bucket(Live(99_999)),
    )
  dict.size(after) |> should.equal(20)
  dict.has_key(after, "loading-more") |> should.equal(True)
  dict.has_key(after, topic(1)) |> should.equal(False)
}

pub fn cap_temporarily_exceeded_when_all_in_flight_test() {
  // 20 Loading buckets — all in flight, none evictable. A 21st insert
  // leaves the cap exceeded rather than dropping an active request;
  // the temporary overflow drains as soon as the first response lands.
  let seeded = {
    use acc, i <- int.range(from: 0, to: 20, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Loading))
  }
  dict.size(seeded) |> should.equal(20)
  let after = bucket_lru.insert_bounded(seeded, "fresh", make_bucket(Loading))
  dict.size(after) |> should.equal(21)
  dict.has_key(after, "fresh") |> should.equal(True)
  dict.has_key(after, topic(0)) |> should.equal(True)
}

pub fn stale_and_loading_more_use_their_timestamps_test() {
  // 19 fresh Live entries (timestamps 1000..1018) + a Stale entry with
  // an older timestamp (500). The Stale one must be evicted on overflow,
  // even though Live and Stale rank by the same field.
  let seeded = {
    use acc, i <- int.range(from: 0, to: 19, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Live(1000 + i)))
  }
  let with_stale =
    bucket_lru.insert_bounded(seeded, "stale", make_bucket(Stale(500)))
  dict.size(with_stale) |> should.equal(20)

  let after =
    bucket_lru.insert_bounded(with_stale, "fresh", make_bucket(LoadingMore(2000)))
  dict.size(after) |> should.equal(20)
  dict.has_key(after, "stale") |> should.equal(False)
  dict.has_key(after, "fresh") |> should.equal(True)
}

pub fn cold_and_failed_also_evict_first_test() {
  // 18 Live + 1 Cold + 1 Failed = 20. Two new inserts evict the two
  // timestamp-less entries before touching any Live bucket.
  let seeded = {
    use acc, i <- int.range(from: 0, to: 18, with: dict.new())
    bucket_lru.insert_bounded(acc, topic(i), make_bucket(Live(1000 + i)))
  }
  let with_cold = bucket_lru.insert_bounded(seeded, "cold", make_bucket(Cold))
  let with_failed =
    bucket_lru.insert_bounded(with_cold, "failed", make_bucket(Failed("oops")))
  dict.size(with_failed) |> should.equal(20)

  let after1 = bucket_lru.insert_bounded(with_failed, "f1", make_bucket(Live(5000)))
  let after2 = bucket_lru.insert_bounded(after1, "f2", make_bucket(Live(5001)))
  dict.size(after2) |> should.equal(20)

  // Both timestamp-less entries are gone; all Live entries survive.
  dict.has_key(after2, "cold") |> should.equal(False)
  dict.has_key(after2, "failed") |> should.equal(False)
  dict.has_key(after2, topic(0)) |> should.equal(True)
}

pub fn max_record_buckets_constant_test() {
  bucket_lru.max_record_buckets |> should.equal(20)
}
