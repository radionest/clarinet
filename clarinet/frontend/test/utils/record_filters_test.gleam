// Unit tests for utils/record_filters
import api/models.{type Record, type User}
import api/types
import clarinet_frontend/i18n.{type Key}
import gleam/dict
import gleam/option.{None, Some}
import gleeunit/should
import utils/record_filters

// Stub translate that just echoes the key constructor name as a tag —
// label content is irrelevant for the assertions in this file.
fn t(_key: Key) -> String {
  ""
}

fn make_record(
  id id: Int,
  status status: types.RecordStatus,
  record_type_name record_type_name: String,
  patient_id patient_id: String,
  user_id user_id: option.Option(String),
) -> Record {
  models.Record(
    id: Some(id),
    context_info: None,
    context_info_html: None,
    status: status,
    study_uid: None,
    series_uid: None,
    record_type_name: record_type_name,
    user_id: user_id,
    patient_id: patient_id,
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
  )
}

fn make_user(id: String, email: String) -> User {
  models.User(
    id: id,
    email: email,
    is_active: True,
    is_superuser: False,
    is_verified: True,
    role_names: [],
    capabilities: [],
  )
}

// --- apply_filters: each dimension in isolation ---

pub fn apply_filters_empty_dict_keeps_all_test() {
  let records = [
    make_record(1, types.Pending, "ct", "p1", None),
    make_record(2, types.Finished, "mri", "p2", Some("u1")),
  ]
  record_filters.apply_filters(records, dict.new())
  |> should.equal(records)
}

pub fn apply_filters_status_test() {
  let r1 = make_record(1, types.Pending, "ct", "p1", None)
  let r2 = make_record(2, types.Finished, "mri", "p2", None)
  record_filters.apply_filters(
    [r1, r2],
    dict.from_list([#("status", "finished")]),
  )
  |> should.equal([r2])
}

pub fn apply_filters_record_type_test() {
  let r1 = make_record(1, types.Pending, "ct", "p1", None)
  let r2 = make_record(2, types.Pending, "mri", "p1", None)
  record_filters.apply_filters(
    [r1, r2],
    dict.from_list([#("record_type", "ct")]),
  )
  |> should.equal([r1])
}

pub fn apply_filters_patient_test() {
  let r1 = make_record(1, types.Pending, "ct", "alice", None)
  let r2 = make_record(2, types.Pending, "ct", "bob", None)
  record_filters.apply_filters(
    [r1, r2],
    dict.from_list([#("patient", "bob")]),
  )
  |> should.equal([r2])
}

// --- apply_filters: "user" key (the new branch) ---

pub fn apply_filters_user_specific_uid_test() {
  let r1 = make_record(1, types.Pending, "ct", "p1", Some("uid-a"))
  let r2 = make_record(2, types.Pending, "ct", "p1", Some("uid-b"))
  let r3 = make_record(3, types.Pending, "ct", "p1", None)
  record_filters.apply_filters(
    [r1, r2, r3],
    dict.from_list([#("user", "uid-a")]),
  )
  |> should.equal([r1])
}

pub fn apply_filters_user_unassigned_matches_none_only_test() {
  let r1 = make_record(1, types.Pending, "ct", "p1", Some("uid-a"))
  let r2 = make_record(2, types.Pending, "ct", "p1", None)
  let r3 = make_record(3, types.Pending, "ct", "p1", None)
  record_filters.apply_filters(
    [r1, r2, r3],
    dict.from_list([#("user", record_filters.unassigned_user_value)]),
  )
  |> should.equal([r2, r3])
}

pub fn apply_filters_user_unknown_uid_excludes_unassigned_test() {
  // A real UUID that no record references must not accidentally
  // collide with the "unassigned" sentinel.
  let r_assigned = make_record(1, types.Pending, "ct", "p1", Some("uid-a"))
  let r_unassigned = make_record(2, types.Pending, "ct", "p1", None)
  record_filters.apply_filters(
    [r_assigned, r_unassigned],
    dict.from_list([#("user", "uid-other")]),
  )
  |> should.equal([])
}

// --- apply_filters: combinations ---

pub fn apply_filters_combined_status_and_user_test() {
  let r1 = make_record(1, types.Pending, "ct", "p1", Some("uid-a"))
  let r2 = make_record(2, types.Finished, "ct", "p1", Some("uid-a"))
  let r3 = make_record(3, types.Finished, "ct", "p1", None)
  record_filters.apply_filters(
    [r1, r2, r3],
    dict.from_list([#("status", "finished"), #("user", "uid-a")]),
  )
  |> should.equal([r2])
}

// --- has_user_filters ---

pub fn has_user_filters_empty_dict_test() {
  record_filters.has_user_filters(dict.new()) |> should.equal(False)
}

pub fn has_user_filters_only_sort_keys_test() {
  // Sort selection alone must NOT make the "Clear filters" button appear.
  dict.from_list([#("sort", "id"), #("sort_dir", "desc")])
  |> record_filters.has_user_filters
  |> should.equal(False)
}

pub fn has_user_filters_status_test() {
  dict.from_list([#("status", "pending")])
  |> record_filters.has_user_filters
  |> should.equal(True)
}

pub fn has_user_filters_user_test() {
  dict.from_list([#("user", record_filters.unassigned_user_value)])
  |> record_filters.has_user_filters
  |> should.equal(True)
}

// --- clear_user_filters preserves sort, drops user filter keys ---

pub fn clear_user_filters_preserves_sort_test() {
  let initial =
    dict.from_list([
      #("status", "pending"),
      #("user", "uid-a"),
      #("sort", "patient"),
      #("sort_dir", "desc"),
    ])
  let cleared = record_filters.clear_user_filters(initial)
  dict.get(cleared, "status") |> should.equal(Error(Nil))
  dict.get(cleared, "user") |> should.equal(Error(Nil))
  dict.get(cleared, "sort") |> should.equal(Ok("patient"))
  dict.get(cleared, "sort_dir") |> should.equal(Ok("desc"))
}

// --- user_options ---
//
// Backend returns distinct user_values (POST /records/filter-options);
// the `__unassigned__` sentinel is prepended server-side when scope
// contains unassigned records. Frontend's job is to resolve display
// labels and prepend the "All Users" slot — these tests cover that.

pub fn user_options_empty_list_returns_only_all_users_test() {
  record_filters.user_options([], dict.new(), t)
  |> should.equal([#("", "")])
}

pub fn user_options_includes_unassigned_when_present_test() {
  let users =
    dict.from_list([#("uid-a", make_user("uid-a", "alice@test"))])
  // Backend places the sentinel at index 0 when applicable.
  let options =
    record_filters.user_options(
      [record_filters.unassigned_user_value, "uid-a"],
      users,
      t,
    )
  case options {
    [first, ..] -> first.0 |> should.equal("")
    [] -> should.fail()
  }
  let values = list_values(options)
  values
  |> contains(record_filters.unassigned_user_value)
  |> should.equal(True)
}

pub fn user_options_skips_unassigned_when_absent_test() {
  let users =
    dict.from_list([#("uid-a", make_user("uid-a", "alice@test"))])
  let values =
    record_filters.user_options(["uid-a"], users, t) |> list_values
  values
  |> contains(record_filters.unassigned_user_value)
  |> should.equal(False)
}

pub fn user_options_falls_back_to_uid_when_user_missing_from_cache_test() {
  // A uid the backend returned isn't loaded into the users cache yet —
  // we still want the dropdown to show *something*, not silently drop it.
  let options = record_filters.user_options(["uid-stale"], dict.new(), t)
  let values = list_values(options)
  values |> contains("uid-stale") |> should.equal(True)
}

pub fn user_options_preserves_backend_order_test() {
  // Dedup happens server-side; frontend does not reorder. Backend returns
  // sorted UUIDs; the frontend builds labels but keeps order intact.
  let users =
    dict.from_list([
      #("uid-a", make_user("uid-a", "alice@test")),
      #("uid-b", make_user("uid-b", "bob@test")),
    ])
  let options =
    record_filters.user_options(["uid-a", "uid-b"], users, t)
  case options {
    [_all, first, second] -> {
      first.0 |> should.equal("uid-a")
      second.0 |> should.equal("uid-b")
    }
    _ -> should.fail()
  }
}

// --- helpers private to this test module ---

fn list_values(options: List(#(String, String))) -> List(String) {
  case options {
    [] -> []
    [head, ..rest] -> [head.0, ..list_values(rest)]
  }
}

fn contains(items: List(String), target: String) -> Bool {
  case items {
    [] -> False
    [head, ..rest] ->
      case head == target {
        True -> True
        False -> contains(rest, target)
      }
  }
}
