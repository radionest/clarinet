// Task execution page with dynamic Formosh forms
import api/models.{type Task, type TaskDesign, type User}
import api/types.{type TaskStatus}
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

// View function for task execution page
pub fn view(model: Model, task_id: String) -> Element(Msg) {
  case dict.get(model.tasks, task_id) {
    Ok(task) -> render_task_execution(model, task)
    Error(_) -> {
      // Task not loaded yet
      loading_view(task_id)
    }
  }
}

// Render the task execution interface
fn render_task_execution(model: Model, task: Task) -> Element(Msg) {
  html.div([attribute.class("task-execution-page")], [
    // Header
    html.div([attribute.class("page-header")], [
      html.h2([], [html.text("Task Execution")]),
      render_task_status(task.status),
    ]),

    // Task information
    html.div([attribute.class("task-info card")], [
      html.h3([], [
        html.text(
          option.map(task.task_design, fn(d) { d.label })
          |> option.flatten
          |> option.unwrap("Task"),
        ),
      ]),
      html.p([attribute.class("task-description")], [
        html.text(
          option.map(task.task_design, fn(d) { d.description })
          |> option.flatten
          |> option.unwrap("Complete the task form below"),
        ),
      ]),
      render_task_metadata(task),
    ]),

    // Dynamic form based on task design's result_schema
    html.div([attribute.class("task-form-container card")], [
      case task.task_design {
        Some(design) -> render_dynamic_form(model, task, design)
        None -> error_view("Task design not found")
      },
    ]),

    // Action buttons
    html.div([attribute.class("page-actions")], [
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(store.Navigate(router.Tasks)),
        ],
        [html.text("Back to Tasks")],
      ),
    ]),
  ])
}

// Render the dynamic form using Formosh
fn render_dynamic_form(
  model: Model,
  task: Task,
  design: TaskDesign,
) -> Element(Msg) {
  case design.result_schema {
    Some(schema_dict) -> {
      // Convert Dict to Json then parse to JsonSchema
      let schema_json = dict_to_json(schema_dict)
      let schema_result = parser.parse_schema(json.to_string(schema_json))

      case schema_result {
        Ok(schema) -> {
          // Check if task can be edited
          let can_edit = can_edit_task(task, model.user)

          // Get existing result data if any
          let initial_data =
            option.map(task.result, fn(r) {
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
                    html.text("Task result viewing temporarily disabled"),
                  ])
                }
                None -> {
                  html.div([attribute.class("no-result")], [
                    html.p([], [html.text("No result submitted yet")]),
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
        html.p([], [html.text("This task does not have a result form defined.")]),
        case task.result {
          Some(result) -> render_raw_result(result)
          None -> html.text("No result submitted.")
        },
      ])
    }
  }
}

// Helper to convert Dict to Json
fn dict_to_json(dict: dict.Dict(String, json.Json)) -> json.Json {
  json.object(dict.to_list(dict))
}

// Check if user can edit task
fn can_edit_task(task: Task, user: Option(User)) -> Bool {
  case task.status {
    types.Pending | types.InWork -> {
      case user {
        Some(u) -> {
          // Check if user is assigned to task or is admin
          case task.user_id {
            Some(assigned_id) -> assigned_id == u.id || u.is_superuser
            None -> u.is_superuser
          }
        }
        None -> False
      }
    }
    _ -> False
    // Cannot edit completed/failed tasks
  }
}

// Render task status badge
fn render_task_status(status: TaskStatus) -> Element(Msg) {
  let #(class, text) = case status {
    types.Pending -> #("badge-pending", "Pending")
    types.InWork -> #("badge-progress", "In Progress")
    types.Finished -> #("badge-success", "Completed")
    types.Failed -> #("badge-danger", "Failed")
    types.Cancelled -> #("badge-secondary", "Cancelled")
    types.Paused -> #("badge-paused", "Paused")
  }

  html.span([attribute.class("badge " <> class)], [html.text(text)])
}

// Render task metadata
fn render_task_metadata(task: Task) -> Element(Msg) {
  html.div([attribute.class("task-metadata")], [
    html.dl([], [
      // Patient ID
      html.dt([], [html.text("Patient:")]),
      html.dd([], [html.text(task.patient_id)]),

      // Study UID
      case task.study_uid {
        Some(uid) ->
          element.fragment([
            html.dt([], [html.text("Study:")]),
            html.dd([], [html.text(uid)]),
          ])
        None -> element.none()
      },

      // Series UID
      case task.series_uid {
        Some(uid) ->
          element.fragment([
            html.dt([], [html.text("Series:")]),
            html.dd([], [html.text(uid)]),
          ])
        None -> element.none()
      },

      // Created date
      case task.created_at {
        Some(date) ->
          element.fragment([
            html.dt([], [html.text("Created:")]),
            html.dd([], [html.text(date)]),
          ])
        None -> element.none()
      },

      // Assigned user
      case task.user {
        Some(user) ->
          element.fragment([
            html.dt([], [html.text("Assigned to:")]),
            html.dd([], [html.text(user.username)]),
          ])
        None -> element.none()
      },
    ]),
  ])
}

// Render raw result as JSON (fallback)
fn render_raw_result(result: dict.Dict(String, json.Json)) -> Element(Msg) {
  html.div([attribute.class("raw-result")], [
    html.h4([], [html.text("Result Data:")]),
    html.pre([attribute.class("json-display")], [
      html.code([], [
        html.text(json.to_string(dict_to_json(result))),
      ]),
    ]),
  ])
}

// Loading view
fn loading_view(task_id: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading task " <> task_id <> "...")]),
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
