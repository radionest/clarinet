// Unit tests for cache/bucket.
import cache/bucket.{
  ChangedAtDesc, IdAsc, IdDesc, ModalityAsc, PatientAsc, PatientDesc,
  RecordTypeAsc, RecordTypeDesc, Records, StatusAsc, StatusDesc, UserAsc,
  UserDesc,
}
import gleam/option.{None, Some}
import gleeunit/should

// --- sort_to_backend_string ---

pub fn sort_to_backend_string_changed_at_desc_test() {
  bucket.sort_to_backend_string(ChangedAtDesc)
  |> should.equal("changed_at_desc")
}

pub fn sort_to_backend_string_id_test() {
  bucket.sort_to_backend_string(IdAsc) |> should.equal("id_asc")
  bucket.sort_to_backend_string(IdDesc) |> should.equal("id_desc")
}

pub fn sort_to_backend_string_record_type_test() {
  bucket.sort_to_backend_string(RecordTypeAsc)
  |> should.equal("record_type_asc")
  bucket.sort_to_backend_string(RecordTypeDesc)
  |> should.equal("record_type_desc")
}

pub fn sort_to_backend_string_status_test() {
  bucket.sort_to_backend_string(StatusAsc) |> should.equal("status_asc")
  bucket.sort_to_backend_string(StatusDesc) |> should.equal("status_desc")
}

pub fn sort_to_backend_string_patient_test() {
  bucket.sort_to_backend_string(PatientAsc) |> should.equal("patient_asc")
  bucket.sort_to_backend_string(PatientDesc) |> should.equal("patient_desc")
}

pub fn sort_to_backend_string_user_test() {
  bucket.sort_to_backend_string(UserAsc) |> should.equal("user_asc")
  bucket.sort_to_backend_string(UserDesc) |> should.equal("user_desc")
}

pub fn sort_to_backend_string_modality_test() {
  bucket.sort_to_backend_string(ModalityAsc) |> should.equal("modality_asc")
  bucket.sort_to_backend_string(bucket.ModalityDesc)
  |> should.equal("modality_desc")
}

// --- default_query / query helpers ---

pub fn default_query_all_none_test() {
  let q = bucket.default_query()
  q.patient_id |> should.equal(None)
  q.study_uid |> should.equal(None)
  q.record_type_name |> should.equal(None)
  q.record_status |> should.equal(None)
  q.user_id |> should.equal(None)
  q.wo_user |> should.equal(None)
  q.sort |> should.equal(ChangedAtDesc)
}

pub fn query_with_patient_test() {
  let q = bucket.query_with_patient("PAT42")
  q.patient_id |> should.equal(Some("PAT42"))
  q.study_uid |> should.equal(None)
  q.record_type_name |> should.equal(None)
}

pub fn query_with_study_test() {
  let q = bucket.query_with_study("1.2.3")
  q.study_uid |> should.equal(Some("1.2.3"))
  q.patient_id |> should.equal(None)
}

pub fn query_with_record_type_test() {
  let q = bucket.query_with_record_type("biopsy")
  q.record_type_name |> should.equal(Some("biopsy"))
  q.patient_id |> should.equal(None)
}

// --- key_to_topic ---

pub fn key_to_topic_default_query_test() {
  // Empty filters → just the sort tag.
  bucket.key_to_topic(Records(bucket.default_query()))
  |> should.equal("records|sort=changed_at_desc")
}

pub fn key_to_topic_includes_patient_test() {
  let topic = bucket.key_to_topic(Records(bucket.query_with_patient("PAT42")))
  topic |> should.equal("records|sort=changed_at_desc|patient=PAT42")
}

pub fn key_to_topic_wo_user_flag_test() {
  let q =
    bucket.RecordsQuery(
      ..bucket.default_query(),
      wo_user: Some(True),
    )
  let topic = bucket.key_to_topic(Records(q))
  topic |> should.equal("records|sort=changed_at_desc|wo_user=1")
}

pub fn key_to_topic_strict_user_filter_test() {
  // An explicit user filter (wo_user: Some(False)) must produce a topic
  // distinct from the unconstrained user scope (wo_user: None) — they are
  // different requests and must not share a cache entry.
  let q =
    bucket.RecordsQuery(
      ..bucket.default_query(),
      user_id: Some("uid-1"),
      wo_user: Some(False),
    )
  let topic = bucket.key_to_topic(Records(q))
  topic |> should.equal("records|sort=changed_at_desc|user=uid-1|wo_user=0")
}

pub fn key_to_topic_user_id_test() {
  let q =
    bucket.RecordsQuery(
      ..bucket.default_query(),
      user_id: Some("uid-1"),
    )
  let topic = bucket.key_to_topic(Records(q))
  topic |> should.equal("records|sort=changed_at_desc|user=uid-1")
}

pub fn key_to_topic_deterministic_test() {
  // Two queries with the same content produce the same topic, regardless of
  // how they were constructed (record-update order doesn't matter).
  let q1 =
    bucket.RecordsQuery(
      patient_id: Some("p"),
      study_uid: None,
      record_type_name: Some("rt"),
      record_status: Some("pending"),
      user_id: None,
      wo_user: None,
      sort: IdAsc,
    )
  let q2 =
    bucket.RecordsQuery(
      ..bucket.default_query(),
      patient_id: Some("p"),
      record_type_name: Some("rt"),
      record_status: Some("pending"),
      sort: IdAsc,
    )
  bucket.key_to_topic(Records(q1))
  |> should.equal(bucket.key_to_topic(Records(q2)))
}

pub fn key_to_topic_differs_for_different_filters_test() {
  let q1 = bucket.query_with_patient("PAT_A")
  let q2 = bucket.query_with_patient("PAT_B")
  let same =
    bucket.key_to_topic(Records(q1)) == bucket.key_to_topic(Records(q2))
  same |> should.be_false
}

pub fn key_to_topic_differs_for_different_sort_test() {
  let q1 = bucket.default_query()
  let q2 = bucket.RecordsQuery(..bucket.default_query(), sort: PatientAsc)
  let same =
    bucket.key_to_topic(Records(q1)) == bucket.key_to_topic(Records(q2))
  same |> should.be_false
}

pub fn key_to_topic_wo_user_keeps_user_id_test() {
  // user_id and wo_user serialize independently (the free-tasks view sends
  // both so the backend can apply unique-per-user exclusions), so the
  // topic must distinguish (Some(uid), Some(True)) from (None, Some(True))
  // — they are different requests.
  let with_uid =
    bucket.RecordsQuery(
      ..bucket.default_query(),
      user_id: Some("uid-1"),
      wo_user: Some(True),
    )
  bucket.key_to_topic(Records(with_uid))
  |> should.equal("records|sort=changed_at_desc|user=uid-1|wo_user=1")
}
