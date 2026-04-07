// Admin Dashboard page — self-contained MVU module
import api/admin as admin_api
import api/models
import api/types
import gleam/dict
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/status

// --- Model ---

pub type Model {
  Model(
    admin_stats: Option(models.AdminStats),
    editing_record_id: Option(Int),
    editing_status_record_id: Option(Int),
    role_matrix: Option(models.RoleMatrix),
    role_toggling: Option(#(String, String)),
  )
}

// --- Msg ---

pub type Msg {
  // Data loading
  AdminStatsLoaded(Result(models.AdminStats, types.ApiError))
  // Record assignment
  ToggleAssignDropdown(record_id: Option(Int))
  AssignUser(record_id: Int, user_id: String)
  AssignUserResult(Result(models.Record, types.ApiError))
  // Status change
  ToggleStatusDropdown(record_id: Option(Int))
  ChangeStatus(record_id: Int, status: String)
  ChangeStatusResult(Result(models.Record, types.ApiError))
  // Role matrix
  RoleMatrixLoaded(Result(models.RoleMatrix, types.ApiError))
  ToggleUserRole(user_id: String, role_name: String, add: Bool)
  UserRoleToggled(Result(Nil, types.ApiError))
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      admin_stats: None,
      editing_record_id: None,
      editing_status_record_id: None,
      role_matrix: None,
      role_toggling: None,
    )
  let effects =
    effect.batch([
      load_effect(admin_api.get_admin_stats, AdminStatsLoaded),
      load_effect(admin_api.get_role_matrix, RoleMatrixLoaded),
    ])
  #(model, effects, [shared.ReloadRecords, shared.ReloadUsers])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    AdminStatsLoaded(Ok(stats)) ->
      #(Model(..model, admin_stats: Some(stats)), effect.none(), [
        shared.SetLoading(False),
      ])

    AdminStatsLoaded(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to load admin statistics"))

    ToggleAssignDropdown(record_id) ->
      #(Model(..model, editing_record_id: record_id), effect.none(), [])

    AssignUser(record_id, user_id) -> {
      let eff = {
        use dispatch <- effect.from
        admin_api.assign_record_user(record_id, user_id)
        |> promise.tap(fn(result) { dispatch(AssignUserResult(result)) })
        Nil
      }
      #(Model(..model, editing_record_id: None), eff, [
        shared.SetLoading(True),
      ])
    }

    AssignUserResult(Ok(record)) ->
      #(model, effect.none(), [
        shared.SetLoading(False),
        shared.CacheRecord(record),
        shared.ShowSuccess("User assigned successfully"),
      ])

    AssignUserResult(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to assign user to record"))

    ToggleStatusDropdown(record_id) ->
      #(
        Model(..model, editing_status_record_id: record_id),
        effect.none(),
        [],
      )

    ChangeStatus(record_id, status_str) -> {
      let eff = {
        use dispatch <- effect.from
        admin_api.update_record_status(record_id, status_str)
        |> promise.tap(fn(result) { dispatch(ChangeStatusResult(result)) })
        Nil
      }
      #(Model(..model, editing_status_record_id: None), eff, [
        shared.SetLoading(True),
      ])
    }

    ChangeStatusResult(Ok(record)) ->
      #(model, effect.none(), [
        shared.SetLoading(False),
        shared.CacheRecord(record),
        shared.ShowSuccess("Status updated successfully"),
      ])

    ChangeStatusResult(Error(err)) ->
      #(
        model,
        effect.none(),
        handle_error(err, "Failed to update record status"),
      )

    RoleMatrixLoaded(Ok(matrix)) ->
      #(Model(..model, role_matrix: Some(matrix)), effect.none(), [
        shared.SetLoading(False),
      ])

    RoleMatrixLoaded(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to load role matrix"))

    ToggleUserRole(user_id, role_name, add) -> {
      let eff = {
        use dispatch <- effect.from
        let api_call = case add {
          True -> admin_api.add_user_role(user_id, role_name)
          False -> admin_api.remove_user_role(user_id, role_name)
        }
        api_call
        |> promise.tap(fn(result) { dispatch(UserRoleToggled(result)) })
        Nil
      }
      #(Model(..model, role_toggling: Some(#(user_id, role_name))), eff, [])
    }

    UserRoleToggled(Ok(_)) -> {
      let eff = load_effect(admin_api.get_role_matrix, RoleMatrixLoaded)
      #(Model(..model, role_toggling: None), eff, [
        shared.ShowSuccess("Role updated successfully"),
      ])
    }

    UserRoleToggled(Error(err)) ->
      #(
        Model(..model, role_toggling: None),
        effect.none(),
        handle_error(err, "Failed to update role"),
      )

  }
}

// --- Helpers ---

fn handle_error(err: types.ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    types.AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

fn load_effect(
  api_call: fn() -> promise.Promise(Result(a, types.ApiError)),
  on_result: fn(Result(a, types.ApiError)) -> Msg,
) -> Effect(Msg) {
  use dispatch <- effect.from
  api_call() |> promise.tap(fn(r) { dispatch(on_result(r)) })
  Nil
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Admin Dashboard")]),
    case model.admin_stats {
      Some(stats) ->
        html.div([attribute.class("dashboard-content")], [
          overview_section(stats),
          status_section(stats),
          roles_section(model),
          records_section(model, shared),
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
      admin_stat_card(
        label: "Records",
        count: stats.total_records,
        color: "green",
      ),
      admin_stat_card(
        label: "Users",
        count: stats.total_users,
        color: "purple",
      ),
      admin_stat_card(
        label: "Patients",
        count: stats.total_patients,
        color: "orange",
      ),
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
          let #(s, count) = pair
          admin_stat_card(label: s, count: count, color: status_color(s))
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
                    |> list.map(fn(user) {
                      role_matrix_row(model, user, roles)
                    }),
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
          event.on_click(ToggleUserRole(user.id, role, !has_role)),
        ]),
      ])
    })
  ])
}

fn records_section(model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.div([attribute.class("section-header")], [
      html.h3([], [html.text("Records")]),
      html.a(
        [
          attribute.class("btn btn-primary"),
          attribute.href(router.route_to_path(router.RecordNew)),
        ],
        [html.text("Create Record")],
      ),
    ]),
    case
      dict.values(shared.cache.records)
      |> list.sort(fn(a, b) {
        int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
      })
    {
      [] ->
        html.p([attribute.class("text-muted")], [
          html.text("No records found."),
        ])
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
              list.map(records, fn(record) {
                record_row(model, shared, record)
              }),
            ),
          ]),
        ])
    },
  ])
}

fn record_row(
  model: Model,
  shared: Shared,
  record: models.Record,
) -> Element(Msg) {
  let record_id = case record.id {
    Some(id) -> id
    None -> 0
  }

  let is_editing = model.editing_record_id == Some(record_id)

  html.tr([], [
    html.td([], [html.text(int.to_string(record_id))]),
    html.td([], [html.text(record.record_type_name)]),
    html.td([], [
      status_cell(
        model: model,
        record_id: record_id,
        status: record.status,
      ),
    ]),
    html.td([], [html.text(record.patient_id)]),
    html.td([], [
      assign_cell(
        shared: shared,
        record_id: record_id,
        user_id: record.user_id,
        is_editing: is_editing,
      ),
    ]),
  ])
}

fn assign_cell(
  shared shared: Shared,
  record_id record_id: Int,
  user_id user_id: Option(String),
  is_editing is_editing: Bool,
) -> Element(Msg) {
  case is_editing {
    True -> user_dropdown(shared, record_id)
    False ->
      case user_id {
        Some(uid) -> {
          let email = case dict.get(shared.cache.users, uid) {
            Ok(user) -> user.email
            Error(_) -> uid
          }
          html.div([attribute.class("assign-cell")], [
            html.span([], [html.text(email)]),
            html.text(" "),
            html.button(
              [
                attribute.class("btn btn-sm btn-outline"),
                event.on_click(ToggleAssignDropdown(Some(record_id))),
              ],
              [html.text("Change")],
            ),
          ])
        }
        None ->
          html.button(
            [
              attribute.class("btn btn-sm btn-primary"),
              event.on_click(ToggleAssignDropdown(Some(record_id))),
            ],
            [html.text("Assign")],
          )
      }
  }
}

fn user_dropdown(shared: Shared, record_id: Int) -> Element(Msg) {
  html.div([attribute.class("assign-dropdown")], [
    html.select(
      [
        attribute.class("form-select form-select-sm"),
        event.on_input(fn(value) {
          case value {
            "" -> ToggleAssignDropdown(None)
            uid -> AssignUser(record_id, uid)
          }
        }),
      ],
      [
        html.option([attribute.value("")], "Select user..."),
        ..dict.values(shared.cache.users)
        |> list.sort(fn(a, b) { string.compare(a.email, b.email) })
        |> list.map(fn(user) {
          html.option([attribute.value(user.id)], user.email)
        })
      ],
    ),
    html.button(
      [
        attribute.class("btn btn-sm btn-outline"),
        event.on_click(ToggleAssignDropdown(None)),
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
  let is_editing = model.editing_status_record_id == Some(record_id)
  let status_str = status.to_backend_string(record_status)
  case is_editing {
    True ->
      html.div([attribute.class("assign-cell")], [
        status_dropdown(record_id),
      ])
    False ->
      html.div([attribute.class("assign-cell")], [
        html.span(
          [attribute.class("badge badge-" <> status_color(status_str))],
          [html.text(status_str)],
        ),
        html.text(" "),
        html.button(
          [
            attribute.class("btn btn-sm btn-outline"),
            event.on_click(ToggleStatusDropdown(Some(record_id))),
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
            "" -> ToggleStatusDropdown(None)
            s -> ChangeStatus(record_id, s)
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
        event.on_click(ToggleStatusDropdown(None)),
      ],
      [html.text("Cancel")],
    ),
  ])
}

fn admin_stat_card(
  label label: String,
  count count: Int,
  color color: String,
) -> Element(Msg) {
  html.div([attribute.class("stat-card card stat-" <> color)], [
    html.div([attribute.class("stat-value")], [
      html.text(int.to_string(count)),
    ]),
    html.div([attribute.class("stat-label")], [html.text(label)]),
  ])
}

fn status_color(s: String) -> String {
  case s {
    "blocked" -> "yellow"
    "pending" -> "blue"
    "inwork" -> "orange"
    "finished" -> "green"
    "failed" -> "red"
    "pause" -> "gray"
    _ -> "blue"
  }
}
