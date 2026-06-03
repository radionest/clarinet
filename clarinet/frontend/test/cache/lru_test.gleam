// Unit tests for cache/lru — record_buckets LRU bound (issue #301).
import cache/bucket.{
  type Bucket, Bucket, Cold, Failed, Live, Loading, LoadingMore, Records, Stale,
}
import cache/lru
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
    lru.insert_bounded(acc, topic(i), make_bucket(Live(i * 1000)))
  }
  dict.size(buckets) |> should.equal(5)
}

pub fn replace_existing_key_does_not_grow_test() {
  let initial =
    dict.new() |> dict.insert(topic(1), make_bucket(Live(100)))
  let result = lru.insert_bounded(initial, topic(1), make_bucket(Live(200)))
  dict.size(result) |> should.equal(1)
}

// --- at cap: oldest evicted ---

pub fn evicts_oldest_when_over_cap_test() {
  // Seed 20 entries with monotonically increasing loaded_at.
  let buckets = {
    use acc, i <- int.range(from: 0, to: 20, with: dict.new())
    lru.insert_bounded(acc, topic(i), make_bucket(Live(1000 + i)))
  }
  dict.size(buckets) |> should.equal(20)

  // 21st insert evicts topic-0 (loaded_at=1000, oldest).
  let after = lru.insert_bounded(buckets, "fresh", make_bucket(Live(99_999)))
  dict.size(after) |> should.equal(20)
  dict.has_key(after, topic(0)) |> should.equal(False)
  dict.has_key(after, "fresh") |> should.equal(True)
}

pub fn protects_just_inserted_topic_test() {
  // Seed 20 entries with NEWER loaded_at values.
  let buckets = {
    use acc, i <- int.range(from: 0, to: 20, with: dict.new())
    lru.insert_bounded(acc, topic(i), make_bucket(Live(10_000 + i)))
  }

  // 21st insert carries the smallest loaded_at but still survives —
  // eviction skips the topic we just inserted.
  let after = lru.insert_bounded(buckets, "fresh", make_bucket(Live(0)))
  dict.has_key(after, "fresh") |> should.equal(True)
  // topic-0 (loaded_at=10_000) is the oldest of the remaining 20 and is dropped.
  dict.has_key(after, topic(0)) |> should.equal(False)
}

// --- buckets without a loaded timestamp sort as 0 ---

pub fn timestamp_less_statuses_evict_first_test() {
  // Seed: 19 Live buckets with timestamps 1..19.
  let seeded = {
    use acc, i <- int.range(from: 1, to: 20, with: dict.new())
    lru.insert_bounded(acc, topic(i), make_bucket(Live(i)))
  }
  // 20th: a Loading bucket (no timestamp → sorts as 0, the weakest).
  let with_loading =
    lru.insert_bounded(seeded, "loading", make_bucket(Loading))
  dict.size(with_loading) |> should.equal(20)

  // 21st insert evicts the Loading bucket, not topic-1 (loaded_at=1).
  let after = lru.insert_bounded(with_loading, "fresh", make_bucket(Live(99)))
  dict.size(after) |> should.equal(20)
  dict.has_key(after, "loading") |> should.equal(False)
  dict.has_key(after, topic(1)) |> should.equal(True)
}

pub fn stale_and_loading_more_use_their_timestamps_test() {
  // 19 fresh Live entries (timestamps 1000..1018) + a Stale entry with
  // an older timestamp (500). The Stale one must be evicted on overflow,
  // even though Live and Stale rank by the same field.
  let seeded = {
    use acc, i <- int.range(from: 0, to: 19, with: dict.new())
    lru.insert_bounded(acc, topic(i), make_bucket(Live(1000 + i)))
  }
  let with_stale =
    lru.insert_bounded(seeded, "stale", make_bucket(Stale(500)))
  dict.size(with_stale) |> should.equal(20)

  let after =
    lru.insert_bounded(with_stale, "fresh", make_bucket(LoadingMore(2000)))
  dict.size(after) |> should.equal(20)
  dict.has_key(after, "stale") |> should.equal(False)
  dict.has_key(after, "fresh") |> should.equal(True)
}

pub fn cold_and_failed_also_evict_first_test() {
  // 18 Live + 1 Cold + 1 Failed = 20. Two new inserts evict the two
  // timestamp-less entries before touching any Live bucket.
  let seeded = {
    use acc, i <- int.range(from: 0, to: 18, with: dict.new())
    lru.insert_bounded(acc, topic(i), make_bucket(Live(1000 + i)))
  }
  let with_cold = lru.insert_bounded(seeded, "cold", make_bucket(Cold))
  let with_failed =
    lru.insert_bounded(with_cold, "failed", make_bucket(Failed("oops")))
  dict.size(with_failed) |> should.equal(20)

  let after1 = lru.insert_bounded(with_failed, "f1", make_bucket(Live(5000)))
  let after2 = lru.insert_bounded(after1, "f2", make_bucket(Live(5001)))
  dict.size(after2) |> should.equal(20)

  // Both timestamp-less entries are gone; all Live entries survive.
  dict.has_key(after2, "cold") |> should.equal(False)
  dict.has_key(after2, "failed") |> should.equal(False)
  dict.has_key(after2, topic(0)) |> should.equal(True)
}

pub fn max_record_buckets_constant_test() {
  lru.max_record_buckets |> should.equal(20)
}
