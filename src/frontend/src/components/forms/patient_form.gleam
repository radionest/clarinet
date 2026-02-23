// Static typed form for Patient creation
import components/forms/base as form
import gleam/dict.{type Dict}
import gleam/option.{Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import router
import store.{type Msg}

// Patient form data type for managing form state
pub type PatientFormData {
  PatientFormData(id: String, name: String)
}

// Message types for form updates
pub type PatientFormMsg {
  UpdatePatientId(String)
  UpdatePatientName(String)
}

// Initialize empty form data
pub fn init() -> PatientFormData {
  PatientFormData(id: "", name: "")
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

    // Patient Name field (required)
    form.field(
      label: "Patient Name",
      name: "patient_name",
      input: form.text_input(
        name: "patient_name",
        value: data.name,
        placeholder: Some("Enter Patient Name"),
        on_input: fn(value) { on_update(UpdatePatientName(value)) },
      ),
      errors: errors,
      required: True,
    ),

    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(text: "Create Patient", disabled: loading, on_click: Some(on_submit())),
      form.cancel_button(text: "Cancel", on_click: store.Navigate(router.Patients)),
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

  let errors = case form.validate_required(value: data.id, field_name: "Patient ID") {
    Error(msg) -> dict.insert(errors, "patient_id", msg)
    Ok(_) -> errors
  }

  let errors = case form.validate_required(value: data.name, field_name: "Patient Name") {
    Error(msg) -> dict.insert(errors, "patient_name", msg)
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
    UpdatePatientName(value) -> PatientFormData(..data, name: value)
  }
}
