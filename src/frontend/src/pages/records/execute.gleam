// Record execution page with dynamic Formosh forms
import api/models.{type Record, type RecordType, type User}
import api/types.{type RecordStatus}
import gleam/dict
import gleam/int
import gleam/json
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event

// import components/formosh_wrapper as formosh  // Temporarily disabled - needs fixing
import router
import schema/parser
import store.{type Model, type Msg}

// View function for record execution page
pub fn view(model: Model, record_id: String) -> Element(Msg) {
  case dict.get(model.records, record_id) {
    Ok(record) -> render_record_execution(model, record)
    Error(_) -> {
      // Record not loaded yet
      loading_view(record_id)
    }
  }
}

// Render the record execution interface
fn render_record_execution(model: Model, record: Record) -> Element(Msg) {
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
        Some(record_type) -> render_dynamic_form(model, record, record_type)
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

// Render the dynamic form using Formosh
fn render_dynamic_form(
  model: Model,
  record: Record,
  record_type: RecordType,
) -> Element(Msg) {
  case record_type.data_schema {
    Some(schema_dict) -> {
      // Convert Dict to Json then parse to JsonSchema
      let schema_json = dict_to_json(schema_dict)
      let schema_result = parser.parse_schema(json.to_string(schema_json))

      case schema_result {
        Ok(schema) -> {
          // Check if record can be edited
          let can_edit = can_edit_record(record, model.user)

          // Get existing data if any
          let initial_data =
            option.map(record.data, fn(r) {
              dict.new()
              // formosh.from_json_dict(r) - TODO: Fix formosh wrapper
            })

          // TODO: Re-enable formosh form rendering when wrapper is fixed
          case can_edit {
            True -> {
              // Editable form - temporarily disabled
              html.div([attribute.class("alert alert-warning")], [
                html.text(
                  "Form rendering temporarily disabled - formosh wrapper needs fixing",
                ),
              ])
            }
            False -> {
              // Read-only view
              case initial_data {
                Some(_data) -> {
                  html.div([attribute.class("alert alert-info")], [
                    html.text("Record data viewing temporarily disabled"),
                  ])
                }
                None -> {
                  html.div([attribute.class("no-data")], [
                    html.p([], [html.text("No data submitted yet")]),
                  ])
                }
              }
            }
          }
        }
        Error(parse_error) -> {
          // Schema parsing failed
          html.div([attribute.class("schema-error")], [
            html.p([], [html.text("Error loading form schema")]),
            html.p([attribute.class("error-details")], [
              html.text(parse_error_to_string(parse_error)),
            ]),
          ])
        }
      }
    }
    None -> {
      // No schema defined - show simple message
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

// Helper to convert Dict to Json
fn dict_to_json(dict: dict.Dict(String, json.Json)) -> json.Json {
  json.object(dict.to_list(dict))
}

// Check if user can edit record
fn can_edit_record(record: Record, user: Option(User)) -> Bool {
  case record.status {
    types.Pending | types.InWork -> {
      case user {
        Some(u) -> {
          // Check if user is assigned to record or is admin
          case record.user_id {
            Some(assigned_id) -> assigned_id == u.id || u.is_superuser
            None -> u.is_superuser
          }
        }
        None -> False
      }
    }
    _ -> False
    // Cannot edit completed/failed records
  }
}

// Render record status badge
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

// Render record metadata
fn render_record_metadata(record: Record) -> Element(Msg) {
  html.div([attribute.class("record-metadata")], [
    html.dl([], [
      // Patient ID
      html.dt([], [html.text("Patient:")]),
      html.dd([], [html.text(record.patient_id)]),

      // Study UID
      case record.study_uid {
        Some(uid) ->
          element.fragment([
            html.dt([], [html.text("Study:")]),
            html.dd([], [html.text(uid)]),
          ])
        None -> element.none()
      },

      // Series UID
      case record.series_uid {
        Some(uid) ->
          element.fragment([
            html.dt([], [html.text("Series:")]),
            html.dd([], [html.text(uid)]),
          ])
        None -> element.none()
      },

      // Created date
      case record.created_at {
        Some(date) ->
          element.fragment([
            html.dt([], [html.text("Created:")]),
            html.dd([], [html.text(date)]),
          ])
        None -> element.none()
      },

      // Assigned user
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

// Render raw data as JSON (fallback)
fn render_raw_data(data: dict.Dict(String, json.Json)) -> Element(Msg) {
  html.div([attribute.class("raw-data")], [
    html.h4([], [html.text("Record Data:")]),
    html.pre([attribute.class("json-display")], [
      html.code([], [
        html.text(json.to_string(dict_to_json(data))),
      ]),
    ]),
  ])
}

// Loading view
fn loading_view(record_id: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading record " <> record_id <> "...")]),
  ])
}

// Error view
fn error_view(message: String) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
  ])
}

// Helper to convert ParseError to string
fn parse_error_to_string(error: parser.ParseError) -> String {
  case error {
    parser.InvalidJson(msg) -> "Invalid JSON: " <> msg
    parser.MissingField(field) -> "Missing field: " <> field
    parser.InvalidType(t) -> "Invalid type: " <> t
    parser.UnexpectedValue(val) -> "Unexpected value: " <> val
    parser.DecodingError(_) -> "Failed to decode JSON Schema"
  }
}
