// Admin Dashboard page
import api/models
import api/types
import utils/status
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
          roles_section(model),
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
      admin_stat_card(label: "Studies", count: stats.total_studies, color: "blue"),
      admin_stat_card(label: "Records", count: stats.total_records, color: "green"),
      admin_stat_card(label: "Users", count: stats.total_users, color: "purple"),
      admin_stat_card(label: "Patients", count: stats.total_patients, color: "orange"),
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
          admin_stat_card(label: status, count: count, color: status_color(status))
        }),
    ),
  ])
}

fn roles_section(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Role Matrix")]),
    case model.role_matrix {
      None ->
        html.p([attribute.class("text-muted")], [
          html.text("Loading role matrix..."),
        ])
      Some(matrix) ->
        case matrix.roles {
          [] ->
            html.p([attribute.class("text-muted")], [
              html.text("No roles defined."),
            ])
          roles ->
            html.div([attribute.class("table-responsive")], [
              html.table([attribute.class("table")], [
                html.thead([], [
                  html.tr(
                    [],
                    [
                      html.th([], [html.text("User")]),
                      ..list.map(roles, fn(role) {
                        html.th([], [html.text(role)])
                      })
                    ],
                  ),
                ]),
                html.tbody(
                  [],
                  matrix.users
                    |> list.sort(fn(a, b) { string.compare(a.email, b.email) })
                    |> list.map(fn(user) { role_matrix_row(model, user, roles) }),
                ),
              ]),
            ])
        }
    },
  ])
}

fn role_matrix_row(
  model: Model,
  user: models.UserRoleInfo,
  roles: List(String),
) -> Element(Msg) {
  let is_inactive = !user.is_active
  let row_class = case is_inactive {
    True -> "text-muted"
    False -> ""
  }

  html.tr([attribute.class(row_class)], [
    html.td([], [
      html.text(user.email),
      case user.is_superuser {
        True ->
          html.span([attribute.class("badge badge-purple")], [
            html.text("admin"),
          ])
        False -> html.text("")
      },
    ]),
    ..list.map(roles, fn(role) {
      let has_role = list.contains(user.role_names, role)
      let is_toggling = model.role_toggling == Some(#(user.id, role))

      html.td([], [
        html.input([
          attribute.type_("checkbox"),
          attribute.class("checkbox-input"),
          attribute.checked(has_role),
          attribute.disabled(is_toggling),
          event.on_click(store.ToggleUserRole(user.id, role, !has_role)),
        ]),
      ])
    })
  ])
}

fn records_section(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Records")]),
    case
      dict.values(model.records)
      |> list.sort(fn(a, b) {
        int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
      })
    {
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

  let is_editing = model.admin_editing_record_id == Some(record_id)

  html.tr([], [
    html.td([], [html.text(int.to_string(record_id))]),
    html.td([], [html.text(record.record_type_name)]),
    html.td([], [
      status_cell(model: model, record_id: record_id, status: record.status),
    ]),
    html.td([], [html.text(record.patient_id)]),
    html.td([], [assign_cell(model: model, record_id: record_id, user_id: record.user_id, is_editing: is_editing)]),
  ])
}

fn assign_cell(
  model model: Model,
  record_id record_id: Int,
  user_id user_id: option.Option(String),
  is_editing is_editing: Bool,
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
        ..dict.values(model.users)
        |> list.sort(fn(a, b) { string.compare(a.email, b.email) })
        |> list.map(fn(user) {
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

fn status_cell(
  model model: Model,
  record_id record_id: Int,
  status record_status: types.RecordStatus,
) -> Element(Msg) {
  let is_editing = model.admin_editing_status_record_id == Some(record_id)
  let status_str = status.to_backend_string(record_status)
  case is_editing {
    True -> html.div([attribute.class("assign-cell")], [status_dropdown(record_id)])
    False ->
      html.div([attribute.class("assign-cell")], [
        html.span([attribute.class("badge badge-" <> status_color(status_str))], [
          html.text(status_str),
        ]),
        html.text(" "),
        html.button(
          [
            attribute.class("btn btn-sm btn-outline"),
            event.on_click(store.AdminToggleStatusDropdown(Some(record_id))),
          ],
          [html.text("Change")],
        ),
      ])
  }
}

fn status_dropdown(record_id: Int) -> Element(Msg) {
  let statuses = ["blocked", "pending", "inwork", "finished", "failed", "pause"]
  html.div([attribute.class("assign-dropdown")], [
    html.select(
      [
        attribute.class("form-select form-select-sm"),
        event.on_input(fn(value) {
          case value {
            "" -> store.AdminToggleStatusDropdown(None)
            s -> store.AdminChangeStatus(record_id, s)
          }
        }),
      ],
      [
        html.option([attribute.value("")], "Select status..."),
        ..list.map(statuses, fn(s) {
          html.option([attribute.value(s)], s)
        })
      ],
    ),
    html.button(
      [
        attribute.class("btn btn-sm btn-outline"),
        event.on_click(store.AdminToggleStatusDropdown(None)),
      ],
      [html.text("Cancel")],
    ),
  ])
}

fn admin_stat_card(label label: String, count count: Int, color color: String) -> Element(Msg) {
  html.div([attribute.class("stat-card card stat-" <> color)], [
    html.div([attribute.class("stat-value")], [
      html.text(int.to_string(count)),
    ]),
    html.div([attribute.class("stat-label")], [html.text(label)]),
  ])
}

fn status_color(status: String) -> String {
  case status {
    "blocked" -> "yellow"
    "pending" -> "blue"
    "inwork" -> "orange"
    "finished" -> "green"
    "failed" -> "red"
    "pause" -> "gray"
    _ -> "blue"
  }
}
