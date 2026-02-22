// Record execution page with dynamic Formosh forms
import api/models.{type Record, type RecordType, type User}
import api/types.{type RecordStatus}
import formosh/component as formosh_component
import gleam/dict
import gleam/dynamic/decode
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}

/// View function for record execution page
pub fn view(model: Model, record_id: String) -> Element(Msg) {
  case dict.get(model.records, record_id) {
    Ok(record) -> render_record_execution(model, record, record_id)
    Error(_) -> loading_view(record_id)
  }
}

/// Render the record execution interface
fn render_record_execution(
  model: Model,
  record: Record,
  record_id: String,
) -> Element(Msg) {
  html.div([attribute.class("record-execution-page")], [
    // Header
    html.div([attribute.class("page-header")], [
      html.h2([], [html.text("Record Execution")]),
      render_record_status(record.status),
    ]),
    // Record information
    html.div([attribute.class("record-info card")], [
      html.h3([], [
        html.text(
          option.map(record.record_type, fn(d) { d.label })
          |> option.flatten
          |> option.unwrap("Record"),
        ),
      ]),
      html.p([attribute.class("record-description")], [
        html.text(
          option.map(record.record_type, fn(d) { d.description })
          |> option.flatten
          |> option.unwrap("Complete the record form below"),
        ),
      ]),
      render_record_metadata(record),
    ]),
    // Dynamic form based on record type's data_schema
    html.div([attribute.class("record-form-container card")], [
      case record.record_type {
        Some(record_type) ->
          render_dynamic_form(model, record, record_type, record_id)
        None -> error_view("Record type not found")
      },
    ]),
    // Action buttons
    html.div([attribute.class("page-actions")], [
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(store.Navigate(router.Records)),
        ],
        [html.text("Back to Records")],
      ),
    ]),
  ])
}

/// Render the dynamic form using Formosh web component
fn render_dynamic_form(
  model: Model,
  record: Record,
  record_type: RecordType,
  record_id: String,
) -> Element(Msg) {
  case record_type.data_schema {
    Some(schema_json) -> {
      let can_edit = can_edit_record(record, model.user)
      case can_edit {
        True -> render_editable_form(schema_json, record_id)
        False -> render_readonly_data(record)
      }
    }
    None -> {
      html.div([attribute.class("no-schema")], [
        html.p([], [html.text("This record does not have a data form defined.")]),
        case record.data {
          Some(data) -> render_raw_data(data)
          None -> html.text("No data submitted.")
        },
      ])
    }
  }
}

/// Render an editable form using the formosh web component
fn render_editable_form(
  schema_json: String,
  record_id: String,
) -> Element(Msg) {
  let submit_url = "/api/records/" <> record_id <> "/data"

  formosh_component.element([
    formosh_component.schema_string(schema_json),
    formosh_component.submit_url(submit_url),
    formosh_component.submit_method("POST"),
    event.on("formosh-submit", decode_form_submit(record_id)),
  ])
}

/// Decode the formosh-submit custom event
fn decode_form_submit(record_id: String) -> decode.Decoder(Msg) {
  use status <- decode.then(decode.at(["detail", "status"], decode.string))

  case status {
    "success" -> decode.success(store.FormSubmitSuccess(record_id))
    _ -> decode.success(store.FormSubmitError("Submission failed"))
  }
}

/// Render read-only data for non-editable records
fn render_readonly_data(record: Record) -> Element(Msg) {
  case record.data {
    Some(data) -> render_raw_data(data)
    None ->
      html.div([attribute.class("no-data")], [
        html.p([], [html.text("No data submitted yet")]),
      ])
  }
}

/// Check if user can edit record
fn can_edit_record(record: Record, user: Option(User)) -> Bool {
  case record.status {
    types.Pending | types.InWork -> {
      case user {
        Some(u) -> {
          case record.user_id {
            Some(assigned_id) -> assigned_id == u.id || u.is_superuser
            None -> u.is_superuser
          }
        }
        None -> False
      }
    }
    _ -> False
  }
}

/// Render record status badge
fn render_record_status(status: RecordStatus) -> Element(Msg) {
  let #(class, text) = case status {
    types.Pending -> #("badge-pending", "Pending")
    types.InWork -> #("badge-progress", "In Progress")
    types.Finished -> #("badge-success", "Completed")
    types.Failed -> #("badge-danger", "Failed")
    types.Paused -> #("badge-paused", "Paused")
  }

  html.span([attribute.class("badge " <> class)], [html.text(text)])
}

/// Render record metadata
fn render_record_metadata(record: Record) -> Element(Msg) {
  html.div([attribute.class("record-metadata")], [
    html.dl([], [
      html.dt([], [html.text("Patient:")]),
      html.dd([], [html.text(record.patient_id)]),
      case record.study_uid {
        Some(uid) ->
          element.fragment([
            html.dt([], [html.text("Study:")]),
            html.dd([], [html.text(uid)]),
          ])
        None -> element.none()
      },
      case record.series_uid {
        Some(uid) ->
          element.fragment([
            html.dt([], [html.text("Series:")]),
            html.dd([], [html.text(uid)]),
          ])
        None -> element.none()
      },
      case record.created_at {
        Some(date) ->
          element.fragment([
            html.dt([], [html.text("Created:")]),
            html.dd([], [html.text(date)]),
          ])
        None -> element.none()
      },
      case record.user {
        Some(user) ->
          element.fragment([
            html.dt([], [html.text("Assigned to:")]),
            html.dd([], [html.text(user.email)]),
          ])
        None -> element.none()
      },
    ]),
  ])
}

/// Render raw data as JSON string (fallback)
fn render_raw_data(data: String) -> Element(Msg) {
  html.div([attribute.class("raw-data")], [
    html.h4([], [html.text("Record Data:")]),
    html.pre([attribute.class("json-display")], [
      html.code([], [html.text(data)]),
    ]),
  ])
}

/// Loading view
fn loading_view(record_id: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading record " <> record_id <> "...")]),
  ])
}

/// Error view
fn error_view(message: String) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
  ])
}
