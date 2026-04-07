// Static typed form for Record creation — generic component (no store dependency)
import api/models.{type Record, type RecordType, type Series, type Study}
import api/types
import components/forms/base as form
import gleam/dict.{type Dict}
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import shared.{type Shared}
import utils/status

// Record form data type for managing form state
pub type RecordFormData {
  RecordFormData(
    record_type_name: String,
    patient_id: String,
    study_uid: String,
    series_uid: String,
    user_id: String,
    parent_record_id: String,
    context_info: String,
  )
}

// Message types for form updates
pub type RecordFormMsg {
  UpdateRecordType(String)
  UpdatePatient(String)
  UpdateStudy(String)
  UpdateSeries(String)
  UpdateUser(String)
  UpdateParentRecordId(String)
  UpdateContextInfo(String)
}

// Initialize empty form data
pub fn init() -> RecordFormData {
  RecordFormData(
    record_type_name: "",
    patient_id: "",
    study_uid: "",
    series_uid: "",
    user_id: "",
    parent_record_id: "",
    context_info: "",
  )
}

// Update form data based on message
pub fn update(data: RecordFormData, msg: RecordFormMsg) -> RecordFormData {
  case msg {
    UpdateRecordType(value) ->
      RecordFormData(
        ..data,
        record_type_name: value,
        study_uid: "",
        series_uid: "",
      )
    UpdatePatient(value) ->
      RecordFormData(
        ..data,
        patient_id: value,
        study_uid: "",
        series_uid: "",
      )
    UpdateStudy(value) ->
      RecordFormData(..data, study_uid: value, series_uid: "")
    UpdateSeries(value) -> RecordFormData(..data, series_uid: value)
    UpdateUser(value) -> RecordFormData(..data, user_id: value)
    UpdateParentRecordId(value) ->
      RecordFormData(..data, parent_record_id: value)
    UpdateContextInfo(value) -> RecordFormData(..data, context_info: value)
  }
}

// Main form view
pub fn view(
  data data: RecordFormData,
  studies studies: List(Study),
  series_list series_list: List(Series),
  errors errors: Dict(String, String),
  loading loading: Bool,
  shared shared: Shared,
  on_update on_update: fn(RecordFormMsg) -> msg,
  on_submit on_submit: fn() -> msg,
  on_cancel on_cancel: msg,
) -> Element(msg) {
  let record_type_options = build_record_type_options(shared.cache.record_types)
  let patient_options = build_patient_options(shared.cache.patients)
  let study_options = build_study_options(studies)
  let series_options = build_series_options(series_list)
  let user_options = build_user_options(shared.cache.users)

  let level = selected_level(data.record_type_name, shared.cache.record_types)

  form.form(on_submit, [
    html.h3([attribute.class("form-title")], [html.text("Record Information")]),
    // Record Type (required)
    form.field(
      label: "Record Type",
      name: "record_type_name",
      input: form.select(
        name: "record_type_name",
        value: data.record_type_name,
        options: [#("", "Select record type..."), ..record_type_options],
        on_change: fn(value) { on_update(UpdateRecordType(value)) },
      ),
      errors: errors,
      required: True,
    ),
    // Patient (required)
    form.field(
      label: "Patient",
      name: "patient_id",
      input: form.select(
        name: "patient_id",
        value: data.patient_id,
        options: [#("", "Select patient..."), ..patient_options],
        on_change: fn(value) { on_update(UpdatePatient(value)) },
      ),
      errors: errors,
      required: True,
    ),
    // Study (conditional on level)
    case needs_study(level) {
      True ->
        form.field(
          label: "Study",
          name: "study_uid",
          input: form.select(
            name: "study_uid",
            value: data.study_uid,
            options: [#("", "Select study..."), ..study_options],
            on_change: fn(value) { on_update(UpdateStudy(value)) },
          ),
          errors: errors,
          required: True,
        )
      False -> html.text("")
    },
    // Series (conditional on level)
    case needs_series(level) {
      True ->
        form.field(
          label: "Series",
          name: "series_uid",
          input: form.select(
            name: "series_uid",
            value: data.series_uid,
            options: [#("", "Select series..."), ..series_options],
            on_change: fn(value) { on_update(UpdateSeries(value)) },
          ),
          errors: errors,
          required: True,
        )
      False -> html.text("")
    },
    // Assigned User (optional)
    form.field(
      label: "Assign to User",
      name: "user_id",
      input: form.select(
        name: "user_id",
        value: data.user_id,
        options: [#("", "No user (unassigned)"), ..user_options],
        on_change: fn(value) { on_update(UpdateUser(value)) },
      ),
      errors: errors,
      required: False,
    ),
    // Parent Record (optional)
    form.field(
      label: "Parent Record",
      name: "parent_record_id",
      input: form.select(
        name: "parent_record_id",
        value: data.parent_record_id,
        options: [
          #("", "No parent record"),
          ..build_parent_record_options(data.patient_id, shared.cache.records)
        ],
        on_change: fn(value) { on_update(UpdateParentRecordId(value)) },
      ),
      errors: errors,
      required: False,
    ),
    // Context Info (optional)
    form.field(
      label: "Context Info",
      name: "context_info",
      input: form.textarea(
        name: "context_info",
        value: data.context_info,
        rows: 3,
        placeholder: Some("Optional notes or context"),
        on_input: fn(value) { on_update(UpdateContextInfo(value)) },
      ),
      errors: errors,
      required: False,
    ),
    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(text: "Create Record", disabled: loading, on_click: None),
      form.cancel_button(text: "Cancel", on_click: on_cancel),
    ]),
    // Loading overlay
    form.loading_overlay(loading),
  ])
}

// Validate form data
pub fn validate(
  data: RecordFormData,
  record_types: Dict(String, RecordType),
) -> Result(Nil, Dict(String, String)) {
  let errors = dict.new()

  let errors = case form.validate_required(
    value: data.record_type_name,
    field_name: "Record Type",
  ) {
    Error(msg) -> dict.insert(errors, "record_type_name", msg)
    Ok(_) -> errors
  }

  let errors = case form.validate_required(
    value: data.patient_id,
    field_name: "Patient",
  ) {
    Error(msg) -> dict.insert(errors, "patient_id", msg)
    Ok(_) -> errors
  }

  let level = selected_level(data.record_type_name, record_types)

  let errors = case needs_study(level) {
    True ->
      case form.validate_required(
        value: data.study_uid,
        field_name: "Study",
      ) {
        Error(msg) -> dict.insert(errors, "study_uid", msg)
        Ok(_) -> errors
      }
    False -> errors
  }

  let errors = case needs_series(level) {
    True ->
      case form.validate_required(
        value: data.series_uid,
        field_name: "Series",
      ) {
        Error(msg) -> dict.insert(errors, "series_uid", msg)
        Ok(_) -> errors
      }
    False -> errors
  }

  case dict.size(errors) {
    0 -> Ok(Nil)
    _ -> Error(errors)
  }
}

// --- Internal helpers ---

fn selected_level(
  record_type_name: String,
  record_types: Dict(String, RecordType),
) -> Option(types.DicomQueryLevel) {
  case dict.get(record_types, record_type_name) {
    Ok(rt) -> Some(rt.level)
    Error(_) -> None
  }
}

fn needs_study(level: Option(types.DicomQueryLevel)) -> Bool {
  case level {
    Some(types.Study) | Some(types.Series) -> True
    _ -> False
  }
}

fn needs_series(level: Option(types.DicomQueryLevel)) -> Bool {
  case level {
    Some(types.Series) -> True
    _ -> False
  }
}

// --- Option builders ---

fn build_record_type_options(
  record_types: Dict(String, RecordType),
) -> List(#(String, String)) {
  record_types
  |> dict.values
  |> list.sort(fn(a, b) { string.compare(a.name, b.name) })
  |> list.map(fn(rt) {
    let label = case rt.label {
      Some(l) -> l <> " (" <> rt.name <> ")"
      None -> rt.name
    }
    #(rt.name, label)
  })
}

fn build_patient_options(
  patients: Dict(String, models.Patient),
) -> List(#(String, String)) {
  patients
  |> dict.values
  |> list.sort(fn(a, b) { string.compare(a.id, b.id) })
  |> list.map(fn(p) {
    let label = case p.name {
      Some(name) -> p.id <> " — " <> name
      None -> p.id
    }
    #(p.id, label)
  })
}

fn build_study_options(studies: List(Study)) -> List(#(String, String)) {
  studies
  |> list.map(fn(s) {
    let label = case s.study_description {
      Some(desc) -> s.study_uid <> " — " <> desc
      None -> s.study_uid
    }
    let label = label <> " (" <> s.date <> ")"
    #(s.study_uid, label)
  })
}

fn build_series_options(series_list: List(Series)) -> List(#(String, String)) {
  series_list
  |> list.map(fn(s) {
    let label = case s.series_description {
      Some(desc) -> desc
      None -> s.series_uid
    }
    let label = case s.modality {
      Some(mod) -> label <> " [" <> mod <> "]"
      None -> label
    }
    let label = label <> " (#" <> int.to_string(s.series_number) <> ")"
    #(s.series_uid, label)
  })
}

fn build_parent_record_options(
  patient_id: String,
  records: Dict(String, Record),
) -> List(#(String, String)) {
  case patient_id {
    "" -> []
    pid ->
      records
      |> dict.values
      |> list.filter(fn(r) { r.patient_id == pid })
      |> list.sort(fn(a, b) {
        int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
      })
      |> list.map(fn(r) {
        let id_str = case r.id {
          Some(id) -> int.to_string(id)
          None -> "?"
        }
        let label =
          "#" <> id_str <> " — " <> r.record_type_name
          <> " (" <> status.display_text(r.status) <> ")"
        #(id_str, label)
      })
  }
}

fn build_user_options(
  users: Dict(String, models.User),
) -> List(#(String, String)) {
  users
  |> dict.values
  |> list.sort(fn(a, b) { string.compare(a.email, b.email) })
  |> list.map(fn(u) { #(u.id, u.email) })
}
