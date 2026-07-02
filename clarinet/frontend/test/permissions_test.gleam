import api/models
import api/records
import api/types
import gleam/json
import gleam/option.{None, Some}
import gleeunit/should
import utils/permissions

fn make_user(role_names: List(String), is_superuser: Bool) -> models.User {
  models.User(
    id: "u1",
    email: "u@test",
    is_active: True,
    is_superuser: is_superuser,
    is_verified: True,
    role_names: role_names,
    capabilities: [],
  )
}

fn make_record(user_id: option.Option(String), shared_editing: Bool) -> models.Record {
  models.Record(
    id: Some(42),
    context_info: None,
    context_info_html: None,
    status: types.Pending,
    study_uid: None,
    series_uid: None,
    record_type_name: "test_type",
    user_id: user_id,
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
    shared_editing: shared_editing,
  )
}

pub fn has_permission_true_when_shared_test() {
  // A non-admin, non-owner user gets permission via the shared flag.
  permissions.has_record_permission(
    Some(make_user(["doctor"], False)),
    make_record(Some("other-user"), True),
  )
  |> should.equal(True)
}

pub fn has_permission_false_when_not_shared_test() {
  permissions.has_record_permission(
    Some(make_user(["doctor"], False)),
    make_record(Some("other-user"), False),
  )
  |> should.equal(False)
}

pub fn decoder_round_trips_shared_editing_test() {
  let payload =
    json.object([
      #("id", json.int(1)),
      #("status", json.string("pending")),
      #("record_type_name", json.string("t")),
      #("patient_id", json.string("P001")),
      #("shared_editing", json.bool(True)),
    ])
    |> json.to_string

  let assert Ok(rec) = json.parse(payload, records.record_decoder())
  rec.shared_editing |> should.equal(True)
}
