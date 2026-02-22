// Static typed form for Patient model
import api/models.{type PatientCreate}
import components/forms/base as form
import gleam/dict.{type Dict}
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Msg}

// Patient form data type for managing form state
pub type PatientFormData {
  PatientFormData(id: String, anon_id: String, anon_name: String)
}

// Message types for form updates
pub type PatientFormMsg {
  UpdatePatientId(String)
  UpdateAnonId(String)
  UpdateAnonName(String)
  SubmitPatient
}

// Convert form data to PatientCreate model
pub fn to_patient_create(data: PatientFormData) -> PatientCreate {
  models.PatientCreate(
    id: data.id,
    anon_id: case data.anon_id {
      "" -> None
      id -> Some(id)
    },
    anon_name: case data.anon_name {
      "" -> None
      name -> Some(name)
    },
  )
}

// Initialize empty form data
pub fn init() -> PatientFormData {
  PatientFormData(id: "", anon_id: "", anon_name: "")
}

// Initialize form data from existing patient
pub fn from_patient(patient: models.Patient) -> PatientFormData {
  PatientFormData(
    id: patient.id,
    anon_id: option.unwrap(patient.anon_id, ""),
    anon_name: option.unwrap(patient.anon_name, ""),
  )
}

// Main form view
pub fn view(
  data data: PatientFormData,
  errors errors: Dict(String, String),
  loading loading: Bool,
  on_update on_update: fn(PatientFormMsg) -> Msg,
  on_submit on_submit: fn() -> Msg,
) -> Element(Msg) {
  form.form(on_submit, [
    html.h3([attribute.class("form-title")], [html.text("Patient Information")]),

    // Patient ID field (required)
    form.field(
      label: "Patient ID",
      name: "patient_id",
      input: form.text_input(
        name: "patient_id",
        value: data.id,
        placeholder: Some("Enter Patient ID"),
        on_input: fn(value) { on_update(UpdatePatientId(value)) },
      ),
      errors: errors,
      required: True,
    ),

    // Anonymous ID field (optional)
    form.field(
      label: "Anonymous ID",
      name: "anon_id",
      input: form.text_input(
        name: "anon_id",
        value: data.anon_id,
        placeholder: Some("Enter Anonymous ID (optional)"),
        on_input: fn(value) { on_update(UpdateAnonId(value)) },
      ),
      errors: errors,
      required: False,
    ),

    // Anonymous Name field (optional)
    form.field(
      label: "Anonymous Name",
      name: "anon_name",
      input: form.text_input(
        name: "anon_name",
        value: data.anon_name,
        placeholder: Some("Enter Anonymous Name (optional)"),
        on_input: fn(value) { on_update(UpdateAnonName(value)) },
      ),
      errors: errors,
      required: False,
    ),

    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(text: "Save Patient", disabled: loading, on_click: Some(on_submit())),
      form.cancel_button(text: "Cancel", on_click: store.Navigate(router.Home)),
    ]),

    // Loading overlay
    form.loading_overlay(loading),
  ])
}

// Validate form data
pub fn validate(
  data: PatientFormData,
) -> Result(PatientFormData, Dict(String, String)) {
  let errors = dict.new()

  // Validate Patient ID (required)
  let errors = case form.validate_required(value: data.id, field_name: "Patient ID") {
    Error(msg) -> dict.insert(errors, "patient_id", msg)
    Ok(_) -> errors
  }

  case dict.size(errors) {
    0 -> Ok(data)
    _ -> Error(errors)
  }
}

// Update form data based on message
pub fn update(data: PatientFormData, msg: PatientFormMsg) -> PatientFormData {
  case msg {
    UpdatePatientId(value) -> PatientFormData(..data, id: value)
    UpdateAnonId(value) -> PatientFormData(..data, anon_id: value)
    UpdateAnonName(value) -> PatientFormData(..data, anon_name: value)
    SubmitPatient -> data
    // Submit is handled by parent component
  }
}

// Import router for navigation
import router
