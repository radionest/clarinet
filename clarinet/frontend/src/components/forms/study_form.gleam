// Static typed form for Study model
import api/models.{type StudyCreate}
import components/forms/base as form
import gleam/dict.{type Dict}
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Msg}

// Study form data type for managing form state
pub type StudyFormData {
  StudyFormData(
    study_uid: String,
    date: String,
    patient_id: String,
    anon_uid: String,
  )
}

// Message types for form updates
pub type StudyFormMsg {
  UpdateStudyUid(String)
  UpdateDate(String)
  UpdatePatientId(String)
  UpdateAnonUid(String)
  SubmitStudy
}

// Convert form data to StudyCreate model
pub fn to_study_create(data: StudyFormData) -> StudyCreate {
  models.StudyCreate(
    study_uid: data.study_uid,
    date: data.date,
    patient_id: data.patient_id,
    anon_uid: case data.anon_uid {
      "" -> None
      uid -> Some(uid)
    },
  )
}

// Initialize empty form data
pub fn init() -> StudyFormData {
  StudyFormData(study_uid: "", date: "", patient_id: "", anon_uid: "")
}

// Initialize form data from existing study
pub fn from_study(study: models.Study) -> StudyFormData {
  StudyFormData(
    study_uid: study.study_uid,
    date: study.date,
    patient_id: study.patient_id,
    anon_uid: option.unwrap(study.anon_uid, ""),
  )
}

// Main form view
pub fn view(
  data data: StudyFormData,
  errors errors: Dict(String, String),
  loading loading: Bool,
  on_update on_update: fn(StudyFormMsg) -> Msg,
  on_submit on_submit: fn() -> Msg,
) -> Element(Msg) {
  form.form(on_submit, [
    html.h3([attribute.class("form-title")], [html.text("Study Information")]),

    // Study UID field (required)
    form.field(
      label: "Study UID",
      name: "study_uid",
      input: form.text_input(
        name: "study_uid",
        value: data.study_uid,
        placeholder: Some("Enter DICOM Study Instance UID"),
        on_input: fn(value) { on_update(UpdateStudyUid(value)) },
      ),
      errors: errors,
      required: True,
    ),

    // Date field (required)
    form.field(
      label: "Study Date",
      name: "date",
      input: form.date_input(name: "date", value: data.date, on_input: fn(value) {
        on_update(UpdateDate(value))
      }),
      errors: errors,
      required: True,
    ),

    // Patient ID field (required)
    form.field(
      label: "Patient ID",
      name: "patient_id",
      input: form.text_input(
        name: "patient_id",
        value: data.patient_id,
        placeholder: Some("Enter Patient ID"),
        on_input: fn(value) { on_update(UpdatePatientId(value)) },
      ),
      errors: errors,
      required: True,
    ),

    // Anonymous UID field (optional)
    form.field(
      label: "Anonymous UID",
      name: "anon_uid",
      input: form.text_input(
        name: "anon_uid",
        value: data.anon_uid,
        placeholder: Some("Enter Anonymous UID (optional)"),
        on_input: fn(value) { on_update(UpdateAnonUid(value)) },
      ),
      errors: errors,
      required: False,
    ),

    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(text: "Save Study", disabled: loading, on_click: Some(on_submit())),
      form.cancel_button(text: "Cancel", on_click: store.Navigate(router.Studies)),
    ]),

    // Loading overlay
    form.loading_overlay(loading),
  ])
}

// Validate form data
pub fn validate(
  data: StudyFormData,
) -> Result(StudyFormData, Dict(String, String)) {
  let errors = dict.new()

  // Validate Study UID
  let errors = case form.validate_required(value: data.study_uid, field_name: "Study UID") {
    Error(msg) -> dict.insert(errors, "study_uid", msg)
    Ok(_) -> errors
  }

  // Validate Date
  let errors = case form.validate_required(value: data.date, field_name: "Study Date") {
    Error(msg) -> dict.insert(errors, "date", msg)
    Ok(_) -> errors
  }

  // Validate Patient ID
  let errors = case form.validate_required(value: data.patient_id, field_name: "Patient ID") {
    Error(msg) -> dict.insert(errors, "patient_id", msg)
    Ok(_) -> errors
  }

  case dict.size(errors) {
    0 -> Ok(data)
    _ -> Error(errors)
  }
}

// Update form data based on message
pub fn update(data: StudyFormData, msg: StudyFormMsg) -> StudyFormData {
  case msg {
    UpdateStudyUid(value) -> StudyFormData(..data, study_uid: value)
    UpdateDate(value) -> StudyFormData(..data, date: value)
    UpdatePatientId(value) -> StudyFormData(..data, patient_id: value)
    UpdateAnonUid(value) -> StudyFormData(..data, anon_uid: value)
    SubmitStudy -> data
    // Submit is handled by parent component
  }
}

// Import router for navigation
import router
