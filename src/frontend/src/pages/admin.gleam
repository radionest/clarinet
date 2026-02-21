// Admin Dashboard page
import api/models
import api/types
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import gleam/string
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Admin Dashboard")]),
    case model.admin_stats {
      Some(stats) ->
        html.div([attribute.class("dashboard-content")], [
          overview_section(stats),
          status_section(stats),
          records_section(model),
        ])
      None ->
        html.div([attribute.class("loading")], [
          html.p([], [html.text("Loading statistics...")]),
        ])
    },
  ])
}

fn overview_section(stats: models.AdminStats) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("System Overview")]),
    html.div([attribute.class("stats-grid")], [
      admin_stat_card("Studies", stats.total_studies, "blue"),
      admin_stat_card("Records", stats.total_records, "green"),
      admin_stat_card("Users", stats.total_users, "purple"),
      admin_stat_card("Patients", stats.total_patients, "orange"),
    ]),
  ])
}

fn status_section(stats: models.AdminStats) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Records by Status")]),
    html.div(
      [attribute.class("stats-grid")],
      stats.records_by_status
        |> dict.to_list
        |> list.sort(fn(a, b) { string.compare(a.0, b.0) })
        |> list.map(fn(pair) {
          let #(status, count) = pair
          admin_stat_card(status, count, status_color(status))
        }),
    ),
  ])
}

fn records_section(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Records")]),
    case model.records_list {
      [] ->
        html.p([attribute.class("text-muted")], [html.text("No records found.")])
      records ->
        html.div([attribute.class("table-responsive")], [
          html.table([attribute.class("table")], [
            html.thead([], [
              html.tr([], [
                html.th([], [html.text("ID")]),
                html.th([], [html.text("Record Type")]),
                html.th([], [html.text("Status")]),
                html.th([], [html.text("Patient")]),
                html.th([], [html.text("Assigned User")]),
              ]),
            ]),
            html.tbody(
              [],
              list.map(records, fn(record) { record_row(model, record) }),
            ),
          ]),
        ])
    },
  ])
}

fn record_row(model: Model, record: models.Record) -> Element(Msg) {
  let record_id = case record.id {
    Some(id) -> id
    None -> 0
  }

  let status_str = case record.status {
    types.Pending -> "pending"
    types.InWork -> "inwork"
    types.Finished -> "finished"
    types.Failed -> "failed"
    types.Paused -> "pause"
    types.Cancelled -> "cancelled"
  }

  let is_editing = model.admin_editing_record_id == Some(record_id)

  html.tr([], [
    html.td([], [html.text(int.to_string(record_id))]),
    html.td([], [html.text(record.record_type_name)]),
    html.td([], [
      html.span([attribute.class("badge badge-" <> status_color(status_str))], [
        html.text(status_str),
      ]),
    ]),
    html.td([], [html.text(record.patient_id)]),
    html.td([], [assign_cell(model, record_id, record.user_id, is_editing)]),
  ])
}

fn assign_cell(
  model: Model,
  record_id: Int,
  user_id: option.Option(String),
  is_editing: Bool,
) -> Element(Msg) {
  case is_editing {
    True -> user_dropdown(model, record_id)
    False ->
      case user_id {
        Some(uid) -> {
          let email = case dict.get(model.users, uid) {
            Ok(user) -> user.email
            Error(_) -> uid
          }
          html.div([attribute.class("assign-cell")], [
            html.span([], [html.text(email)]),
            html.text(" "),
            html.button(
              [
                attribute.class("btn btn-sm btn-outline"),
                event.on_click(store.AdminToggleAssignDropdown(Some(record_id))),
              ],
              [html.text("Change")],
            ),
          ])
        }
        None ->
          html.button(
            [
              attribute.class("btn btn-sm btn-primary"),
              event.on_click(store.AdminToggleAssignDropdown(Some(record_id))),
            ],
            [html.text("Assign")],
          )
      }
  }
}

fn user_dropdown(model: Model, record_id: Int) -> Element(Msg) {
  html.div([attribute.class("assign-dropdown")], [
    html.select(
      [
        attribute.class("form-select form-select-sm"),
        event.on_input(fn(value) {
          case value {
            "" -> store.AdminToggleAssignDropdown(None)
            uid -> store.AdminAssignUser(record_id, uid)
          }
        }),
      ],
      [
        html.option([attribute.value("")], "Select user..."),
        ..list.map(model.users_list, fn(user) {
          html.option([attribute.value(user.id)], user.email)
        })
      ],
    ),
    html.button(
      [
        attribute.class("btn btn-sm btn-outline"),
        event.on_click(store.AdminToggleAssignDropdown(None)),
      ],
      [html.text("Cancel")],
    ),
  ])
}

fn admin_stat_card(label: String, count: Int, color: String) -> Element(Msg) {
  html.div([attribute.class("stat-card card stat-" <> color)], [
    html.div([attribute.class("stat-value")], [
      html.text(int.to_string(count)),
    ]),
    html.div([attribute.class("stat-label")], [html.text(label)]),
  ])
}

fn status_color(status: String) -> String {
  case status {
    "pending" -> "blue"
    "inwork" -> "orange"
    "finished" -> "green"
    "failed" -> "red"
    "pause" -> "gray"
    _ -> "blue"
  }
}
