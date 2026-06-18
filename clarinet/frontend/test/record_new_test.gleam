// Unit tests for the pure functions of `pages/records/new.gleam`.
// `compute_locked_fields` / `compute_hidden_fields` are pure projections of
// `HostMode`; `init_modal` is exercised via a minimal synthetic `Shared`.
import api/types
import cache
import clarinet_frontend/i18n
import gleam/list
import gleam/option.{None, Some}
import gleeunit/should
import pages/records/new as record_new
import router
import shared

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
  record_new.compute_hidden_fields(record_new.FullPage, False)
  |> should.equal([])
}

pub fn hidden_fields_modal_test() {
  let hidden =
    record_new.compute_hidden_fields(
      record_new.Modal(shared.PatientArgs(patient_id: "P001")),
      False,
    )
  // Modal hides the optional user picker and parent-record picker.
  should.be_true(list.contains(hidden, "user_id"))
  should.be_true(list.contains(hidden, "parent_record_id"))
  should.equal(list.length(hidden), 2)
}

pub fn hidden_fields_modal_parent_required_test() {
  // When the selected RecordType demands a parent and the modal mode does
  // not preset one (Patient/Study/Series context), the parent picker
  // must surface — only `user_id` stays hidden.
  let hidden =
    record_new.compute_hidden_fields(
      record_new.Modal(shared.PatientArgs(patient_id: "P001")),
      True,
    )
  should.be_true(list.contains(hidden, "user_id"))
  should.be_false(list.contains(hidden, "parent_record_id"))
  should.equal(list.length(hidden), 1)
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

// --- RecordArgs (create-from-Record) ---

pub fn locked_fields_record_args_series_test() {
  // Source Record at SERIES level — all UIDs filled → all UIDs locked.
  let locked =
    record_new.compute_locked_fields(
      record_new.Modal(shared.RecordArgs(
        patient_id: "P001",
        study_uid: Some("1.2.3"),
        series_uid: Some("1.2.3.4"),
        parent_id: 42,
        context_info_prefill: "from #42",
      )),
    )
  should.be_true(list.contains(locked, "patient_id"))
  should.be_true(list.contains(locked, "study_uid"))
  should.be_true(list.contains(locked, "series_uid"))
  should.equal(list.length(locked), 3)
}

pub fn locked_fields_record_args_study_test() {
  // Source Record at STUDY level — series_uid absent → not locked.
  let locked =
    record_new.compute_locked_fields(
      record_new.Modal(shared.RecordArgs(
        patient_id: "P001",
        study_uid: Some("1.2.3"),
        series_uid: None,
        parent_id: 42,
        context_info_prefill: "from #42",
      )),
    )
  should.be_true(list.contains(locked, "patient_id"))
  should.be_true(list.contains(locked, "study_uid"))
  should.be_false(list.contains(locked, "series_uid"))
  should.equal(list.length(locked), 2)
}

pub fn locked_fields_record_args_patient_test() {
  // Source Record at PATIENT level — only patient_id locked.
  let locked =
    record_new.compute_locked_fields(
      record_new.Modal(shared.RecordArgs(
        patient_id: "P001",
        study_uid: None,
        series_uid: None,
        parent_id: 42,
        context_info_prefill: "from #42",
      )),
    )
  should.equal(locked, ["patient_id"])
}

pub fn hidden_fields_record_args_test() {
  // parent_record_id stays hidden under RecordArgs — value is preset from
  // args, surfaced via the read-only header pill, never via a picker.
  // ``parent_required=True`` does not override this: the parent is already
  // pinned by args.
  let hidden =
    record_new.compute_hidden_fields(
      record_new.Modal(shared.RecordArgs(
        patient_id: "P001",
        study_uid: None,
        series_uid: None,
        parent_id: 42,
        context_info_prefill: "from #42",
      )),
      True,
    )
  should.be_true(list.contains(hidden, "user_id"))
  should.be_true(list.contains(hidden, "parent_record_id"))
  should.equal(list.length(hidden), 2)
}

pub fn init_modal_record_args_prefill_test() {
  let args =
    shared.RecordArgs(
      patient_id: "P001",
      study_uid: Some("1.2.3"),
      series_uid: Some("1.2.3.4"),
      parent_id: 42,
      context_info_prefill: "Created from foo (id=42)",
    )
  let #(model, _eff, out_msgs) = record_new.init_modal(args, make_shared())
  should.equal(model.form_data.patient_id, "P001")
  should.equal(model.form_data.study_uid, "1.2.3")
  should.equal(model.form_data.series_uid, "1.2.3.4")
  should.equal(model.form_data.parent_record_id, "42")
  should.equal(model.form_data.context_info, "Created from foo (id=42)")
  should.equal(model.host_mode, record_new.Modal(args))
  should.be_true(list.contains(out_msgs, shared.ReloadRecordTypes))
}

pub fn init_modal_record_args_optional_uids_test() {
  // Optional UIDs surface as empty strings in form_data (the form treats
  // "" as unselected; the cascading picker then drives subsequent values).
  let args =
    shared.RecordArgs(
      patient_id: "P001",
      study_uid: None,
      series_uid: None,
      parent_id: 7,
      context_info_prefill: "",
    )
  let #(model, _eff, _out_msgs) = record_new.init_modal(args, make_shared())
  should.equal(model.form_data.study_uid, "")
  should.equal(model.form_data.series_uid, "")
  should.equal(model.form_data.parent_record_id, "7")
}

// --- expected_level_for ---

pub fn expected_level_full_page_test() {
  record_new.expected_level_for(record_new.FullPage)
  |> should.equal(None)
}

pub fn expected_level_patient_args_test() {
  record_new.expected_level_for(
    record_new.Modal(shared.PatientArgs(patient_id: "P001")),
  )
  |> should.equal(Some(types.Patient))
}

pub fn expected_level_study_args_test() {
  record_new.expected_level_for(
    record_new.Modal(shared.StudyArgs(
      patient_id: "P001",
      study_uid: "1.2.3",
    )),
  )
  |> should.equal(Some(types.Study))
}

pub fn expected_level_series_args_test() {
  record_new.expected_level_for(
    record_new.Modal(shared.SeriesArgs(
      patient_id: "P001",
      study_uid: "1.2.3",
      series_uid: "1.2.3.4",
    )),
  )
  |> should.equal(Some(types.Series))
}

pub fn expected_level_record_args_series_test() {
  record_new.expected_level_for(
    record_new.Modal(shared.RecordArgs(
      patient_id: "P001",
      study_uid: Some("1.2.3"),
      series_uid: Some("1.2.3.4"),
      parent_id: 1,
      context_info_prefill: "",
    )),
  )
  |> should.equal(Some(types.Series))
}

pub fn expected_level_record_args_study_test() {
  record_new.expected_level_for(
    record_new.Modal(shared.RecordArgs(
      patient_id: "P001",
      study_uid: Some("1.2.3"),
      series_uid: None,
      parent_id: 1,
      context_info_prefill: "",
    )),
  )
  |> should.equal(Some(types.Study))
}

pub fn expected_level_record_args_patient_test() {
  record_new.expected_level_for(
    record_new.Modal(shared.RecordArgs(
      patient_id: "P001",
      study_uid: None,
      series_uid: None,
      parent_id: 1,
      context_info_prefill: "",
    )),
  )
  |> should.equal(Some(types.Patient))
}
