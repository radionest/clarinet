// Static typed form for Patient creation
import clarinet_frontend/i18n.{type Key}
import components/forms/base as form
import gleam/dict.{type Dict}
import gleam/list
import gleam/option.{None, Some}
import gleam/string
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html

// DICOM PatientID (tag 0010,0020, VR=LO): A-Z a-z 0-9 . _ - ^, max 64 chars,
// no whitespace. Mirrors clarinet/models/patient.py::PATIENT_ID_PATTERN.
const patient_id_allowed_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-^"

fn is_valid_patient_id(value: String) -> Bool {
  let len = string.length(value)
  case len > 0 && len <= 64 {
    False -> False
    True ->
      value
      |> string.to_graphemes
      |> list.all(fn(c) { string.contains(patient_id_allowed_chars, c) })
  }
}

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
  on_update on_update: fn(PatientFormMsg) -> msg,
  on_submit on_submit: fn() -> msg,
  on_cancel on_cancel: msg,
  translate translate: fn(Key) -> String,
) -> Element(msg) {
  form.form(on_submit, [
    html.h3([attribute.class("form-title")], [html.text(translate(i18n.FormPatientInfo))]),

    // Patient ID field (required) + DICOM-format hint
    html.div([], [
      form.field(
        label: translate(i18n.FormPatientId),
        name: "patient_id",
        input: form.text_input(
          name: "patient_id",
          value: data.id,
          placeholder: Some(translate(i18n.FormPatientIdPlaceholder)),
          on_input: fn(value) { on_update(UpdatePatientId(value)) },
        ),
        errors: errors,
        required: True,
      ),
      html.small([attribute.class("form-hint")], [
        html.text(translate(i18n.FormPatientIdHint)),
      ]),
    ]),

    // Patient Name field (required)
    form.field(
      label: translate(i18n.FormPatientName),
      name: "patient_name",
      input: form.text_input(
        name: "patient_name",
        value: data.name,
        placeholder: Some(translate(i18n.FormPatientNamePlaceholder)),
        on_input: fn(value) { on_update(UpdatePatientName(value)) },
      ),
      errors: errors,
      required: True,
    ),

    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(text: translate(i18n.FormBtnCreatePatient), disabled: loading, on_click: None),
      form.cancel_button(text: translate(i18n.BtnCancel), on_click: on_cancel),
    ]),

    // Loading overlay
    form.loading_overlay(loading),
  ])
}

// Validate form data. Trims ``id`` before checking — leading/trailing
// whitespace is corrected silently; inner whitespace and disallowed chars
// fail with the localized DICOM-format message.
pub fn validate(
  data: PatientFormData,
  translate: fn(Key) -> String,
) -> Result(PatientFormData, Dict(String, String)) {
  let trimmed_id = string.trim(data.id)
  let errors = dict.new()

  let errors = case form.validate_required(value: trimmed_id, field_name: "Patient ID") {
    Error(msg) -> dict.insert(errors, "patient_id", msg)
    Ok(_) ->
      case is_valid_patient_id(trimmed_id) {
        True -> errors
        False ->
          dict.insert(errors, "patient_id", translate(i18n.FormPatientIdInvalid))
      }
  }

  let errors = case form.validate_required(value: data.name, field_name: "Patient Name") {
    Error(msg) -> dict.insert(errors, "patient_name", msg)
    Ok(_) -> errors
  }

  case dict.size(errors) {
    0 -> Ok(PatientFormData(id: trimmed_id, name: data.name))
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
