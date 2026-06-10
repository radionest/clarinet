// Unit tests for back-navigation on the record execute page: NavigateBack,
// DeleteResult, CompleteRecordResult and FormSubmitSuccess follow
// shared.previous_route when set and fall back to the Records list (or stay
// on the page for auto-return) when entered by direct URL. Effects are not
// executed — only the returned OutMsg list is asserted (same approach as
// unassign_user_test).
import api/models
import api/types
import cache
import clarinet_frontend/i18n
import gleam/dict
import gleam/option.{type Option, None, Some}
import gleeunit/should
import pages/records/execute
import router
import shared

fn make_shared(previous_route: Option(router.Route)) -> shared.Shared {
  shared.Shared(
    user: None,
    route: router.RecordDetail("42"),
    previous_route: previous_route,
    project_name: "",
    project_description: "",
    cache: cache.init(),
    viewers: [],
    translate: fn(_) { "" },
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
    is_editable: True,
  )
}

fn make_execute_model(record_id: String) -> execute.Model {
  let #(model, _eff, _out) = execute.init(record_id, make_shared(None))
  model
}

// --- NavigateBack ---

pub fn navigate_back_uses_previous_route_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.NavigateBack,
      make_shared(Some(router.PatientDetail("p1"))),
    )
  out |> should.equal([shared.Navigate(router.PatientDetail("p1"))])
}

pub fn navigate_back_falls_back_to_records_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.NavigateBack,
      make_shared(None),
    )
  out |> should.equal([shared.Navigate(router.Records(dict.new()))])
}

// --- CompleteRecordResult ---

pub fn complete_ok_navigates_to_previous_route_test() {
  let record = make_record()
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.CompleteRecordResult(Ok(record)),
      make_shared(Some(router.PatientDetail("p1"))),
    )
  out
  |> should.equal([
    shared.SetLoading(False),
    shared.CacheRecord(record),
    shared.ShowSuccess("Record completed successfully"),
    shared.ReloadRecord("42"),
    shared.Navigate(router.PatientDetail("p1")),
  ])
}

pub fn complete_ok_without_previous_route_stays_test() {
  let record = make_record()
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.CompleteRecordResult(Ok(record)),
      make_shared(None),
    )
  out
  |> should.equal([
    shared.SetLoading(False),
    shared.CacheRecord(record),
    shared.ShowSuccess("Record completed successfully"),
    shared.ReloadRecord("42"),
  ])
}

// --- FormSubmitSuccess ---

pub fn form_submit_success_navigates_to_previous_route_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.FormSubmitSuccess,
      make_shared(Some(router.StudyDetail("1.2.3"))),
    )
  out
  |> should.equal([
    shared.ShowSuccess("Record data submitted successfully"),
    shared.ReloadRecord("42"),
    shared.Navigate(router.StudyDetail("1.2.3")),
  ])
}

pub fn form_submit_success_without_previous_route_stays_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.FormSubmitSuccess,
      make_shared(None),
    )
  out
  |> should.equal([
    shared.ShowSuccess("Record data submitted successfully"),
    shared.ReloadRecord("42"),
  ])
}

// --- DeleteResult ---

pub fn delete_ok_navigates_to_previous_route_test() {
  let #(_model, _eff, out) =
    execute.update(
      make_execute_model("42"),
      execute.DeleteResult(Ok(Nil)),
      make_shared(Some(router.PatientDetail("p1"))),
    )
  out
  |> should.equal([
    shared.SetLoading(False),
    shared.InvalidateAllRecordBuckets,
    shared.ShowSuccess("Record deleted successfully"),
    shared.Navigate(router.PatientDetail("p1")),
  ])
}
