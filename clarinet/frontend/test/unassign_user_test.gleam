// Unit tests for admin dashboard update arms (unassign + online presence) and
// the record execute page. Effects are not executed — only the returned Model
// and OutMsg list are asserted (same approach as record_new_test).
import api/models
import api/types
import cache
import clarinet_frontend/i18n
import gleam/dict
import gleam/option.{None, Some}
import gleam/set
import gleeunit/should
import pages/admin
import pages/records/execute
import router
import shared
import utils/load_status

fn make_shared() -> shared.Shared {
  shared.Shared(
    user: None,
    route: router.Home,
    previous_route: None,
    project_name: "",
    project_description: "",
    cache: cache.init(),
    viewers: [],
    anon_per_study: False,
    dicomweb_backend: "builtin",
    translate: fn(_) { "translated" },
    locale: i18n.En,
  )
}

fn make_record() -> models.Record {
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
  )
}

fn make_admin_model() -> admin.Model {
  admin.Model(
    admin_stats: None,
    stats_status: load_status.Loaded,
    editing_record_id: Some(42),
    editing_status_record_id: None,
    role_matrix: None,
    matrix_status: load_status.Loaded,
    role_toggling: None,
    online_user_ids: set.new(),
    active_filters: dict.new(),
  )
}

fn make_execute_model(record_id: String) -> execute.Model {
  let #(model, _eff, _out) = execute.init(record_id, make_shared())
  model
}

// --- admin dashboard page ---

pub fn admin_unassign_closes_dropdown_and_sets_loading_test() {
  let #(model, _eff, out) =
    admin.update(make_admin_model(), admin.UnassignUser(42), make_shared())
  model.editing_record_id |> should.equal(None)
  out |> should.equal([shared.SetLoading(True)])
}

pub fn admin_unassign_ok_refreshes_stats_and_caches_test() {
  let record = make_record()
  let #(model, _eff, out) =
    admin.update(
      make_admin_model(),
      admin.UnassignUserResult(Ok(record)),
      make_shared(),
    )
  model.stats_status |> should.equal(load_status.Loading)
  out
  |> should.equal([
    shared.SetLoading(False),
    shared.CacheRecord(record),
    shared.ShowSuccess("translated"),
  ])
}

pub fn admin_unassign_auth_error_logs_out_test() {
  let #(_model, _eff, out) =
    admin.update(
      make_admin_model(),
      admin.UnassignUserResult(Error(types.AuthError("expired"))),
      make_shared(),
    )
  out |> should.equal([shared.Logout])
}

pub fn admin_online_users_loaded_sets_ids_test() {
  let #(model, _eff, out) =
    admin.update(
      make_admin_model(),
      admin.OnlineUsersLoaded(Ok(["u1", "u2"])),
      make_shared(),
    )
  model.online_user_ids |> set.contains("u1") |> should.be_true
  model.online_user_ids |> set.contains("u2") |> should.be_true
  out |> should.equal([])
}

pub fn admin_presence_online_inserts_user_test() {
  let #(model, _eff, out) =
    admin.update(make_admin_model(), admin.PresenceChanged("u9", True), make_shared())
  model.online_user_ids |> set.contains("u9") |> should.be_true
  out |> should.equal([])
}

pub fn admin_presence_offline_removes_user_test() {
  let start =
    admin.Model(..make_admin_model(), online_user_ids: set.from_list(["u9"]))
  let #(model, _eff, out) =
    admin.update(start, admin.PresenceChanged("u9", False), make_shared())
  model.online_user_ids |> set.contains("u9") |> should.be_false
  out |> should.equal([])
}

// --- record execute page ---

pub fn execute_request_unassign_opens_confirm_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.RequestUnassign,
      make_shared(),
    )
  out |> should.equal([shared.OpenDeleteConfirm("record-user", "42")])
}

pub fn execute_unassign_sets_loading_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.UnassignUser,
      make_shared(),
    )
  out |> should.equal([shared.SetLoading(True)])
}

pub fn execute_unassign_invalid_record_id_is_noop_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("abc"),
      execute.UnassignUser,
      make_shared(),
    )
  out |> should.equal([])
}

pub fn execute_unassign_ok_caches_record_and_toasts_test() {
  let record = make_record()
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.UnassignUserResult(Ok(record)),
      make_shared(),
    )
  out
  |> should.equal([
    shared.SetLoading(False),
    shared.CacheRecord(record),
    shared.ShowSuccess("translated"),
  ])
}

pub fn execute_unassign_auth_error_logs_out_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.UnassignUserResult(Error(types.AuthError("expired"))),
      make_shared(),
    )
  out |> should.equal([shared.Logout])
}
