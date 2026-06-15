import api/models
import api/sse_events
import api/types
import cache
import cache/bucket
import gleam/dict
import gleam/option.{None, Some}
import gleeunit/should

fn make_record(id: Int) -> models.Record {
  models.Record(
    id: Some(id),
    context_info: None,
    context_info_html: None,
    status: types.Pending,
    study_uid: None,
    series_uid: None,
    record_type_name: "test_type",
    user_id: None,
    patient_id: "P001",
    parent_record_id: None,
    study_anon_uid: None,
    series_anon_uid: None,
    viewer_study_uids: None,
    viewer_series_uids: None,
    clarinet_storage_path: None,
    files: None,
    file_checksums: None,
    file_links: None,
    patient: None,
    study: None,
    series: None,
    record_type: None,
    data: None,
    created_at: None,
    changed_at: None,
    started_at: None,
    finished_at: None,
    radiant: None,
    display_anon_id: None,
    is_editable: True,
  )
}

fn with_bucket(
  model: cache.Model,
  key: bucket.BucketKey,
  status: bucket.BucketStatus,
  items: List(models.Record),
) -> cache.Model {
  let b =
    bucket.Bucket(key: key, status: status, items: items, next_cursor: None)
  cache.Model(
    ..model,
    record_buckets: dict.insert(
      model.record_buckets,
      bucket.key_to_topic(key),
      b,
    ),
  )
}

fn entity(entity: String, action: sse_events.Action, id: String) -> cache.Msg {
  cache.SseEntityEvent(sse_events.EntityEvent(
    entity: entity,
    action: action,
    id: id,
    record_type_name: None,
    user_id: None,
  ))
}

pub fn record_deleted_removes_from_cache_and_buckets_test() {
  let rec = make_record(42)
  let key = bucket.Records(bucket.query_with_patient("P001"))
  let model =
    cache.init()
    |> cache.put_record(rec)
    |> with_bucket(key, bucket.Live(0), [rec])

  let #(new_model, _eff, _out) =
    cache.update(model, entity("record", sse_events.Deleted, "42"))

  dict.has_key(new_model.records, "42") |> should.equal(False)
  cache.bucket_items(new_model, key) |> should.equal([])
}

pub fn record_update_marks_only_live_buckets_stale_test() {
  let live_key = bucket.Records(bucket.query_with_patient("PL"))
  let failed_key = bucket.Records(bucket.query_with_patient("PF"))
  let model =
    cache.init()
    |> with_bucket(live_key, bucket.Live(0), [])
    |> with_bucket(failed_key, bucket.Failed("boom"), [])

  let #(new_model, _eff, _out) =
    cache.update(model, entity("record", sse_events.Updated, "999"))

  cache.bucket_status(new_model, live_key) |> should.equal(bucket.Stale(0))
  cache.bucket_status(new_model, failed_key)
  |> should.equal(bucket.Failed("boom"))
  new_model.sse_debounce |> should.equal(True)
}

pub fn record_refetched_error_drops_record_test() {
  let model = cache.put_record(cache.init(), make_record(7))
  let #(new_model, _eff, _out) =
    cache.update(
      model,
      cache.SseRecordRefetched("7", Error(types.AuthError("gone"))),
    )
  dict.has_key(new_model.records, "7") |> should.equal(False)
}
