// Unit tests for the pure functions of `pages/records/new.gleam`.
// `compute_locked_fields` / `compute_hidden_fields` are pure projections of
// `HostMode`; `init_modal` is exercised via a minimal synthetic `Shared`.
// Also covers the parent picker it renders: `record_form.parent_record_groups`
// and the parent reset in `record_form.update`.
import api/models
import api/types
import cache
import clarinet_frontend/i18n
import components/forms/record_form
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/string
import gleeunit/should
import lustre/element
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
    dicomweb_backend: "builtin",
    registration_enabled: False,
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

// --- Parent picker: any record of the patient, whatever its level/study ---

fn make_study(
  uid: String,
  date: String,
  description: Option(String),
) -> models.Study {
  models.Study(
    study_uid: uid,
    date: date,
    anon_uid: None,
    study_description: description,
    modalities_in_study: None,
    patient_id: "P001",
    patient: None,
    series: None,
    records: None,
  )
}

fn make_record(
  id: Int,
  record_type_name: String,
  study: Option(models.Study),
) -> models.Record {
  models.Record(
    id: Some(id),
    context_info: None,
    context_info_html: None,
    status: types.Finished,
    study_uid: option.map(study, fn(s) { s.study_uid }),
    series_uid: None,
    record_type_name: record_type_name,
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
    study: study,
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

fn make_record_type(name: String, label: Option(String)) -> models.RecordType {
  models.RecordType(
    name: name,
    description: None,
    label: label,
    slicer_script: None,
    slicer_script_args: None,
    slicer_result_validator: None,
    slicer_result_validator_args: None,
    data_schema: None,
    ui_schema: None,
    role_name: None,
    max_records: None,
    min_records: None,
    unique_by: None,
    parent_required: False,
    inherit_user_from_parent: False,
    editable: True,
    edit_window_days: None,
    viewer_mode: "single_series",
    allowed_viewers: None,
    level: types.Study,
    file_registry: None,
    constraint_role: None,
    records: None,
  )
}

// The clarinet_nir_ablation chain: the pre-ablation MRI (study 1.1) parents a
// PATIENT-level ablation, which parents an XA control on study 1.2; the
// control MRI (study 1.3) is where the new record is being created.
fn study_pre() -> models.Study {
  make_study("1.1", "2026-02-03", Some("MRI liver"))
}

fn pre_ablation() -> models.Record {
  make_record(41, "mri-pre-ablation", Some(study_pre()))
}

fn ablation() -> models.Record {
  make_record(52, "ablation", None)
}

fn parent_candidates() -> List(models.Record) {
  let study_xa = make_study("1.2", "2026-02-20", None)
  let study_control = make_study("1.3", "2026-05-12", Some("MRI control"))
  let series_arterial =
    models.Series(
      series_uid: "1.3.1",
      series_description: Some("Arterial"),
      series_number: 3,
      modality: Some("CT"),
      instance_count: None,
      anon_uid: None,
      study_uid: "1.3",
      study: None,
      records: None,
    )
  let series_record =
    models.Record(
      ..make_record(71, "compare", Some(study_control)),
      series_uid: Some("1.3.1"),
      series: Some(series_arterial),
    )
  // Deliberately out of order: grouping must not depend on input order.
  [
    series_record,
    pre_ablation(),
    make_record(60, "xa-ablation-control", Some(study_xa)),
    ablation(),
    make_record(70, "first-check", Some(study_control)),
  ]
}

pub fn parent_groups_with_study_test() {
  // A STUDY/SERIES-level child: its own study first, then patient-level
  // records, then the other studies newest first.
  record_form.parent_record_groups(parent_candidates(), [], "1.3")
  |> should.equal([
    #("This study", [
      #("70", "#70 · first-check · Completed"),
      #("71", "#71 · compare · Arterial [CT] (#3) · Completed"),
    ]),
    #("Patient level", [#("52", "#52 · ablation · Completed")]),
    #("Study 2026-02-20", [#("60", "#60 · xa-ablation-control · Completed")]),
    #("Study 2026-02-03 · MRI liver", [
      #("41", "#41 · mri-pre-ablation · Completed"),
    ]),
  ])
}

pub fn parent_groups_without_study_test() {
  // A PATIENT-level child (no study picked): patient-level records first,
  // then every study newest first — no "This study" group.
  record_form.parent_record_groups(parent_candidates(), [], "")
  |> should.equal([
    #("Patient level", [#("52", "#52 · ablation · Completed")]),
    #("Study 2026-05-12 · MRI control", [
      #("70", "#70 · first-check · Completed"),
      #("71", "#71 · compare · Arterial [CT] (#3) · Completed"),
    ]),
    #("Study 2026-02-20", [#("60", "#60 · xa-ablation-control · Completed")]),
    #("Study 2026-02-03 · MRI liver", [
      #("41", "#41 · mri-pre-ablation · Completed"),
    ]),
  ])
}

pub fn parent_option_uses_record_type_label_test() {
  let labeled =
    models.Record(
      ..pre_ablation(),
      record_type: Some(make_record_type(
        "mri-pre-ablation",
        Some("MRI before ablation"),
      )),
    )
  record_form.parent_record_groups([labeled], [], "")
  |> should.equal([
    #("Study 2026-02-03 · MRI liver", [
      #("41", "#41 · MRI before ablation · Completed"),
    ]),
  ])
}

pub fn parent_groups_unmask_study_test() {
  // A non-superuser gets a masked record back: anon study UID, sentinel date,
  // no description. The patient's (unmasked) studies map it to the real
  // study, so it joins its unmasked sibling in one group — "This study" here.
  let masked =
    make_record(42, "masked-type", Some(make_study("2.1", "1976-01-01", None)))
  let studies = [models.Study(..study_pre(), anon_uid: Some("2.1"))]
  record_form.parent_record_groups([masked, pre_ablation()], studies, "1.1")
  |> should.equal([
    #("This study", [
      #("41", "#41 · mri-pre-ablation · Completed"),
      #("42", "#42 · masked-type · Completed"),
    ]),
  ])
  record_form.parent_record_groups([masked, pre_ablation()], studies, "")
  |> should.equal([
    #("Study 2026-02-03 · MRI liver", [
      #("41", "#41 · mri-pre-ablation · Completed"),
      #("42", "#42 · masked-type · Completed"),
    ]),
  ])
}

pub fn update_patient_clears_parent_record_test() {
  // A parent picked for patient A must not survive a switch to patient B —
  // it would be submitted as a cross-patient parent.
  let data =
    record_form.RecordFormData(
      ..record_form.init(),
      patient_id: "P001",
      parent_record_id: "41",
    )
  record_form.update(data, record_form.UpdatePatient("P002")).parent_record_id
  |> should.equal("")
}

pub fn switching_patient_drops_previous_candidates_test() {
  let s = make_shared()
  let #(m, _, _) = record_new.init(s)
  let #(m, _, _) =
    record_new.update(
      m,
      record_new.UpdateForm(record_form.UpdatePatient("P001")),
      s,
    )
  let #(m, _, _) =
    record_new.update(
      m,
      record_new.ParentCandidatesLoaded("P001", Ok([ablation()])),
      s,
    )
  m.form_parent_candidates |> should.equal([ablation()])

  let #(m, _, _) =
    record_new.update(
      m,
      record_new.UpdateForm(record_form.UpdatePatient("P002")),
      s,
    )
  m.form_parent_candidates |> should.equal([])
  // A late response for the previous patient must not repopulate the picker.
  let #(m, _, _) =
    record_new.update(
      m,
      record_new.ParentCandidatesLoaded("P001", Ok([ablation()])),
      s,
    )
  m.form_parent_candidates |> should.equal([])
}

pub fn view_renders_grouped_parent_picker_test() {
  let s = make_shared()
  let #(m, _, _) = record_new.init(s)
  let #(m, _, _) =
    record_new.update(
      m,
      record_new.UpdateForm(record_form.UpdatePatient("P001")),
      s,
    )
  let #(m, _, _) =
    record_new.update(
      m,
      record_new.ParentCandidatesLoaded(
        "P001",
        Ok([pre_ablation(), ablation()]),
      ),
      s,
    )
  let #(m, _, _) =
    record_new.update(
      m,
      record_new.UpdateForm(record_form.UpdateParentRecordId("41")),
      s,
    )
  let html = record_new.view(m, s) |> element.to_string
  html
  |> string.contains("<optgroup label=\"Patient level\">")
  |> should.be_true
  // Lustre sorts attributes when rendering, so match the option by value
  // rather than by a fixed attribute order.
  let tag = option_tag(html, "41")
  tag |> string.contains(" selected") |> should.be_true
  tag
  |> string.contains(">#41 · mri-pre-ablation · Completed</option>")
  |> should.be_true
}

// The rendered `<option ...>label</option>` whose value is `value`, or "".
fn option_tag(html: String, value: String) -> String {
  html
  |> string.split("<option")
  |> list.find(string.contains(_, "value=\"" <> value <> "\""))
  |> result.unwrap("")
}
