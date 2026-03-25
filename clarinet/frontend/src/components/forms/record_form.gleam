// Static typed form for Record creation (admin)
import api/types
import gleam/string
import utils/status
import components/forms/base as form
import gleam/dict.{type Dict}
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import router
import store.{type Model, type Msg}

/// Get the selected record type's level from model
fn selected_level(model: Model) -> Option(types.DicomQueryLevel) {
  case dict.get(model.record_types, model.record_form_record_type_name) {
    Ok(rt) -> Some(rt.level)
    Error(_) -> None
  }
}

/// Whether the study field should be shown
fn needs_study(model: Model) -> Bool {
  case selected_level(model) {
    Some(types.Study) | Some(types.Series) -> True
    _ -> False
  }
}

/// Whether the series field should be shown
fn needs_series(model: Model) -> Bool {
  case selected_level(model) {
    Some(types.Series) -> True
    _ -> False
  }
}

/// Main form view
pub fn view(model: Model) -> Element(Msg) {
  let record_type_options = build_record_type_options(model)
  let patient_options = build_patient_options(model)
  let study_options = build_study_options(model)
  let series_options = build_series_options(model)
  let user_options = build_user_options(model)

  form.form(fn() { store.SubmitRecordForm }, [
    html.h3([attribute.class("form-title")], [html.text("Record Information")]),
    // Record Type (required)
    form.field(
      label: "Record Type",
      name: "record_type_name",
      input: form.select(
        name: "record_type_name",
        value: model.record_form_record_type_name,
        options: [#("", "Select record type..."), ..record_type_options],
        on_change: fn(value) { store.UpdateRecordFormRecordType(value) },
      ),
      errors: model.form_errors,
      required: True,
    ),
    // Patient (required)
    form.field(
      label: "Patient",
      name: "patient_id",
      input: form.select(
        name: "patient_id",
        value: model.record_form_patient_id,
        options: [#("", "Select patient..."), ..patient_options],
        on_change: fn(value) { store.UpdateRecordFormPatient(value) },
      ),
      errors: model.form_errors,
      required: True,
    ),
    // Study (conditional on level)
    case needs_study(model) {
      True ->
        form.field(
          label: "Study",
          name: "study_uid",
          input: form.select(
            name: "study_uid",
            value: model.record_form_study_uid,
            options: [#("", "Select study..."), ..study_options],
            on_change: fn(value) { store.UpdateRecordFormStudy(value) },
          ),
          errors: model.form_errors,
          required: True,
        )
      False -> html.text("")
    },
    // Series (conditional on level)
    case needs_series(model) {
      True ->
        form.field(
          label: "Series",
          name: "series_uid",
          input: form.select(
            name: "series_uid",
            value: model.record_form_series_uid,
            options: [#("", "Select series..."), ..series_options],
            on_change: fn(value) { store.UpdateRecordFormSeries(value) },
          ),
          errors: model.form_errors,
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
        value: model.record_form_user_id,
        options: [#("", "No user (unassigned)"), ..user_options],
        on_change: fn(value) { store.UpdateRecordFormUser(value) },
      ),
      errors: model.form_errors,
      required: False,
    ),
    // Parent Record (optional)
    form.field(
      label: "Parent Record",
      name: "parent_record_id",
      input: form.select(
        name: "parent_record_id",
        value: model.record_form_parent_record_id,
        options: [
          #("", "No parent record"),
          ..build_parent_record_options(model)
        ],
        on_change: fn(value) { store.UpdateRecordFormParentRecordId(value) },
      ),
      errors: model.form_errors,
      required: False,
    ),
    // Context Info (optional)
    form.field(
      label: "Context Info",
      name: "context_info",
      input: form.textarea(
        name: "context_info",
        value: model.record_form_context_info,
        rows: 3,
        placeholder: Some("Optional notes or context"),
        on_input: fn(value) { store.UpdateRecordFormContextInfo(value) },
      ),
      errors: model.form_errors,
      required: False,
    ),
    // Form actions
    html.div([attribute.class("form-actions")], [
      form.submit_button(text: "Create Record", disabled: model.loading, on_click: None),
      form.cancel_button(text: "Cancel", on_click: store.Navigate(router.AdminDashboard)),
    ]),
    // Loading overlay
    form.loading_overlay(model.loading),
  ])
}

/// Validate the form, returning errors dict if invalid
pub fn validate(model: Model) -> Result(Nil, Dict(String, String)) {
  let errors = dict.new()

  let errors = case form.validate_required(
    value: model.record_form_record_type_name,
    field_name: "Record Type",
  ) {
    Error(msg) -> dict.insert(errors, "record_type_name", msg)
    Ok(_) -> errors
  }

  let errors = case form.validate_required(
    value: model.record_form_patient_id,
    field_name: "Patient",
  ) {
    Error(msg) -> dict.insert(errors, "patient_id", msg)
    Ok(_) -> errors
  }

  let errors = case needs_study(model) {
    True ->
      case form.validate_required(
        value: model.record_form_study_uid,
        field_name: "Study",
      ) {
        Error(msg) -> dict.insert(errors, "study_uid", msg)
        Ok(_) -> errors
      }
    False -> errors
  }

  let errors = case needs_series(model) {
    True ->
      case form.validate_required(
        value: model.record_form_series_uid,
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

// --- Option builders ---

fn build_record_type_options(model: Model) -> List(#(String, String)) {
  model.record_types
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

fn build_patient_options(model: Model) -> List(#(String, String)) {
  model.patients
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

fn build_study_options(model: Model) -> List(#(String, String)) {
  model.record_form_studies
  |> list.map(fn(s) {
    let label = case s.study_description {
      Some(desc) -> s.study_uid <> " — " <> desc
      None -> s.study_uid
    }
    let label = label <> " (" <> s.date <> ")"
    #(s.study_uid, label)
  })
}

fn build_series_options(model: Model) -> List(#(String, String)) {
  model.record_form_series
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

fn build_parent_record_options(model: Model) -> List(#(String, String)) {
  case model.record_form_patient_id {
    "" -> []
    patient_id ->
      model.records
      |> dict.values
      |> list.filter(fn(r) { r.patient_id == patient_id })
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

fn build_user_options(model: Model) -> List(#(String, String)) {
  model.users
  |> dict.values
  |> list.sort(fn(a, b) { string.compare(a.email, b.email) })
  |> list.map(fn(u) { #(u.id, u.email) })
}
