// Static typed form for Study model
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import gleam/option.{type Option, None, Some}
import gleam/dict.{type Dict}
import api/models.{type StudyCreate}
import components/forms/base as form
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
  StudyFormData(
    study_uid: "",
    date: "",
    patient_id: "",
    anon_uid: "",
  )
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
  data: StudyFormData,
  errors: Dict(String, String),
  loading: Bool,
  on_update: fn(StudyFormMsg) -> Msg,
  on_submit: fn() -> Msg,
) -> Element(Msg) {
  form.form(on_submit, [
    html.h3([attribute.class("form-title")], [html.text("Study Information")]),

    // Study UID field (required)
    form.required_field(
      "Study UID",
      "study_uid",
      form.text_input(
        "study_uid",
        data.study_uid,
        Some("Enter DICOM Study Instance UID"),
        fn(value) { on_update(UpdateStudyUid(value)) },
      ),
      errors,
    ),

    // Date field (required)
    form.required_field(
      "Study Date",
      "date",
      form.date_input(
        "date",
        data.date,
        fn(value) { on_update(UpdateDate(value)) },
      ),
      errors,
    ),

    // Patient ID field (required)
    form.required_field(
      "Patient ID",
      "patient_id",
      form.text_input(
        "patient_id",
        data.patient_id,
        Some("Enter Patient ID"),
        fn(value) { on_update(UpdatePatientId(value)) },
      ),
      errors,
    ),

    // Anonymous UID field (optional)
    form.field(
      "Anonymous UID",
      "anon_uid",
      form.text_input(
        "anon_uid",
        data.anon_uid,
        Some("Enter Anonymous UID (optional)"),
        fn(value) { on_update(UpdateAnonUid(value)) },
      ),
      errors,
    ),

    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button("Save Study", loading, Some(on_submit())),
      form.cancel_button("Cancel", store.Navigate(router.Studies)),
    ]),

    // Loading overlay
    form.loading_overlay(loading),
  ])
}

// Validate form data
pub fn validate(data: StudyFormData) -> Result(StudyFormData, Dict(String, String)) {
  let errors = dict.new()

  // Validate Study UID
  let errors = case form.validate_required(data.study_uid, "Study UID") {
    Error(msg) -> dict.insert(errors, "study_uid", msg)
    Ok(_) -> errors
  }

  // Validate Date
  let errors = case form.validate_required(data.date, "Study Date") {
    Error(msg) -> dict.insert(errors, "date", msg)
    Ok(_) -> errors
  }

  // Validate Patient ID
  let errors = case form.validate_required(data.patient_id, "Patient ID") {
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
    SubmitStudy -> data  // Submit is handled by parent component
  }
}

// Import router for navigation
import router