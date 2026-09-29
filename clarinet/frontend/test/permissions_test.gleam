import api/models
import api/records
import api/types
import gleam/json
import gleam/option.{None, Some}
import gleeunit/should
import utils/permissions

fn make_record(allowed: List(String)) -> models.Record {
  models.Record(
    id: Some(42),
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
    shared_editing: False,
    allowed_commands: allowed,
  )
}

pub fn edit_follows_the_server_test() {
  permissions.can_edit_record(make_record(["edit"]), None) |> should.equal(True)
  permissions.can_edit_record(make_record([]), None) |> should.equal(False)
}

pub fn fill_needs_submit_and_an_open_status_test() {
  permissions.can_fill_record(make_record(["submit"]), None)
  |> should.equal(True)
  permissions.can_fill_record(
    models.Record(..make_record(["submit"]), status: types.Finished),
    None,
  )
  |> should.equal(False)
  permissions.can_fill_record(make_record([]), None) |> should.equal(False)
}

pub fn release_follows_the_server_test() {
  permissions.can_release_record(make_record(["unassign"]))
  |> should.equal(True)
  permissions.can_release_record(make_record(["claim"])) |> should.equal(False)
}

pub fn decoder_reads_allowed_commands_test() {
  let payload =
    json.object([
      #("id", json.int(1)),
      #("status", json.string("pending")),
      #("record_type_name", json.string("t")),
      #("patient_id", json.string("P001")),
      #("allowed_commands", json.array(["edit", "fail"], json.string)),
    ])
    |> json.to_string
  let assert Ok(rec) = json.parse(payload, records.record_decoder())
  rec.allowed_commands |> should.equal(["edit", "fail"])
}

pub fn decoder_defaults_allowed_commands_to_empty_test() {
  let payload =
    json.object([
      #("id", json.int(1)),
      #("status", json.string("pending")),
      #("record_type_name", json.string("t")),
      #("patient_id", json.string("P001")),
    ])
    |> json.to_string
  let assert Ok(rec) = json.parse(payload, records.record_decoder())
  rec.allowed_commands |> should.equal([])
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
