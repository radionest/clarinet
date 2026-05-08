// Unit tests for the pure functions of `pages/records/new.gleam`.
// `compute_locked_fields` / `compute_hidden_fields` are pure projections of
// `HostMode`; `init_modal` is exercised via a minimal synthetic `Shared`.
import cache
import clarinet_frontend/i18n
import gleam/list
import gleam/option.{None}
import gleeunit/should
import pages/records/new as record_new
import router
import shared

fn make_shared() -> shared.Shared {
  shared.Shared(
    user: None,
    route: router.Home,
    project_name: "",
    project_description: "",
    cache: cache.init(),
    viewers: [],
    translate: fn(_) { "" },
    locale: i18n.En,
  )
}

// --- compute_locked_fields ---

pub fn locked_fields_full_page_test() {
  record_new.compute_locked_fields(record_new.FullPage)
  |> should.equal([])
}

pub fn locked_fields_patient_args_test() {
  record_new.compute_locked_fields(
    record_new.Modal(shared.PatientArgs(patient_id: "P001")),
  )
  |> should.equal(["patient_id"])
}

pub fn locked_fields_study_args_test() {
  let locked =
    record_new.compute_locked_fields(
      record_new.Modal(shared.StudyArgs(
        patient_id: "P001",
        study_uid: "1.2.3",
      )),
    )
  // Order doesn't matter to consumers; assert membership.
  should.be_true(list.contains(locked, "patient_id"))
  should.be_true(list.contains(locked, "study_uid"))
  should.equal(list.length(locked), 2)
}

pub fn locked_fields_series_args_test() {
  let locked =
    record_new.compute_locked_fields(
      record_new.Modal(shared.SeriesArgs(
        patient_id: "P001",
        study_uid: "1.2.3",
        series_uid: "1.2.3.4",
      )),
    )
  should.be_true(list.contains(locked, "patient_id"))
  should.be_true(list.contains(locked, "study_uid"))
  should.be_true(list.contains(locked, "series_uid"))
  should.equal(list.length(locked), 3)
}

// --- compute_hidden_fields ---

pub fn hidden_fields_full_page_test() {
  record_new.compute_hidden_fields(record_new.FullPage)
  |> should.equal([])
}

pub fn hidden_fields_modal_test() {
  let hidden =
    record_new.compute_hidden_fields(
      record_new.Modal(shared.PatientArgs(patient_id: "P001")),
    )
  // Modal hides the optional user picker and parent-record picker.
  should.be_true(list.contains(hidden, "user_id"))
  should.be_true(list.contains(hidden, "parent_record_id"))
  should.equal(list.length(hidden), 2)
}

// --- init_modal ---

pub fn init_modal_patient_prefill_test() {
  let args = shared.PatientArgs(patient_id: "P001")
  let #(model, _eff, out_msgs) = record_new.init_modal(args, make_shared())
  // form_data carries the patient_id from args; study/series stay blank.
  should.equal(model.form_data.patient_id, "P001")
  should.equal(model.form_data.study_uid, "")
  should.equal(model.form_data.series_uid, "")
  // host_mode reflects the modal context.
  should.equal(model.host_mode, record_new.Modal(args))
  // RecordTypes is the only universally required reload.
  should.be_true(list.contains(out_msgs, shared.ReloadRecordTypes))
}

pub fn init_modal_study_prefill_test() {
  let args = shared.StudyArgs(patient_id: "P001", study_uid: "1.2.3")
  let #(model, _eff, _out_msgs) = record_new.init_modal(args, make_shared())
  should.equal(model.form_data.patient_id, "P001")
  should.equal(model.form_data.study_uid, "1.2.3")
  should.equal(model.form_data.series_uid, "")
}

pub fn init_modal_series_prefill_test() {
  let args =
    shared.SeriesArgs(
      patient_id: "P001",
      study_uid: "1.2.3",
      series_uid: "1.2.3.4",
    )
  let #(model, _eff, _out_msgs) = record_new.init_modal(args, make_shared())
  should.equal(model.form_data.patient_id, "P001")
  should.equal(model.form_data.study_uid, "1.2.3")
  should.equal(model.form_data.series_uid, "1.2.3.4")
}
