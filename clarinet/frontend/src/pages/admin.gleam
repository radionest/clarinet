// Admin Dashboard page — self-contained MVU module
import api/admin as admin_api
import api/models
import api/types
import cache
import cache/bucket
import clarinet_frontend/i18n.{type Key}
import components/records_list
import components/status_badge
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/set
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}
import utils/record_filters
import utils/records_list_state
import utils/records_query
import utils/status
import utils/table_sort

// --- Model ---

pub type Model {
  Model(
    admin_stats: Option(models.AdminStats),
    stats_status: LoadStatus,
    editing_record_id: Option(Int),
    editing_status_record_id: Option(Int),
    role_matrix: Option(models.RoleMatrix),
    matrix_status: LoadStatus,
    role_toggling: Option(#(String, String)),
    online_user_ids: set.Set(String),
    active_filters: Dict(String, String),
  )
}

// --- Msg ---

pub type Msg {
  // Data loading
  AdminStatsLoaded(Result(models.AdminStats, types.ApiError))
  RetryLoadStats
  // Record assignment
  ToggleAssignDropdown(record_id: Option(Int))
  AssignUser(record_id: Int, user_id: String)
  AssignUserResult(Result(models.Record, types.ApiError))
  UnassignUser(record_id: Int)
  UnassignUserResult(Result(models.Record, types.ApiError))
  // Status change
  ToggleStatusDropdown(record_id: Option(Int))
  ChangeStatus(record_id: Int, status: String)
  ChangeStatusResult(Result(models.Record, types.ApiError))
  // Role matrix
  RoleMatrixLoaded(Result(models.RoleMatrix, types.ApiError))
  RetryLoadMatrix
  ToggleUserRole(user_id: String, role_name: String, add: Bool)
  UserRoleToggled(Result(Nil, types.ApiError))
  // Online presence (role matrix dots)
  OnlineUsersLoaded(Result(List(String), types.ApiError))
  PresenceChanged(user_id: String, online: Bool)
  // Records filters / sort
  AddFilter(key: String, value: String)
  RemoveFilter(key: String)
  ClearFilters
  ColumnHeaderClicked(column: String)
}

const storage_key = "admin.records.filters"

// --- Init ---

pub fn init(
  filters: Dict(String, String),
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let #(effective_filters, filters_fx) =
    records_list_state.resolve_initial_filters(
      filters,
      storage_key,
      router.AdminDashboard,
      [],
    )

  let model =
    Model(
      admin_stats: None,
      stats_status: load_status.Loading,
      editing_record_id: None,
      editing_status_record_id: None,
      role_matrix: None,
      matrix_status: load_status.Loading,
      role_toggling: None,
      online_user_ids: set.new(),
      active_filters: effective_filters,
    )
  let effects =
    effect.batch([
      load_effect(admin_api.get_admin_stats, AdminStatsLoaded),
      load_effect(admin_api.get_role_matrix, RoleMatrixLoaded),
      load_effect(admin_api.get_online_users, OnlineUsersLoaded),
      filters_fx,
    ])
  #(model, effects, [
    shared.FetchBucket(bucket_key_for(effective_filters)),
    shared.ReloadUsers,
    shared.ReloadFilterOptions,
  ])
}

fn bucket_key_for(filters: Dict(String, String)) -> bucket.BucketKey {
  bucket.Records(records_query.from_filters(filters))
}

// Shared success path for record mutations (assign/unassign/status change):
// refresh admin stats — `unassigned_records`, `records_by_status` and similar
// derived counts that the cards display become stale otherwise.
fn mutation_success(
  model: Model,
  record: models.Record,
  toast: String,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let stats_eff = load_effect(admin_api.get_admin_stats, AdminStatsLoaded)
  #(Model(..model, stats_status: load_status.Loading), stats_eff, [
    shared.SetLoading(False),
    shared.CacheRecord(record),
    shared.ShowSuccess(toast),
  ])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    AdminStatsLoaded(Ok(stats)) -> #(
      Model(..model, admin_stats: Some(stats), stats_status: load_status.Loaded),
      effect.none(),
      [shared.SetLoading(False)],
    )

    AdminStatsLoaded(Error(err)) -> #(
      Model(
        ..model,
        stats_status: load_status.Failed("Failed to load admin statistics"),
      ),
      effect.none(),
      handle_error(err, "Failed to load admin statistics"),
    )

    RetryLoadStats -> #(
      Model(..model, stats_status: load_status.Loading),
      load_effect(admin_api.get_admin_stats, AdminStatsLoaded),
      [],
    )

    ToggleAssignDropdown(record_id) -> #(
      Model(..model, editing_record_id: record_id),
      effect.none(),
      [],
    )

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
      mutation_success(
        model,
        record,
        shared.translate(i18n.AdminMsgUserAssigned),
      )

    AssignUserResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to assign user to record"),
    )

    UnassignUser(record_id) -> {
      let eff = {
        use dispatch <- effect.from
        admin_api.unassign_record_user(record_id)
        |> promise.tap(fn(result) { dispatch(UnassignUserResult(result)) })
        Nil
      }
      #(Model(..model, editing_record_id: None), eff, [
        shared.SetLoading(True),
      ])
    }

    UnassignUserResult(Ok(record)) ->
      mutation_success(
        model,
        record,
        shared.translate(i18n.AdminMsgUserUnassigned),
      )

    UnassignUserResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to unassign user from record"),
    )

    ToggleStatusDropdown(record_id) -> #(
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
      mutation_success(
        model,
        record,
        shared.translate(i18n.AdminMsgStatusUpdated),
      )

    ChangeStatusResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to update record status"),
    )

    RoleMatrixLoaded(Ok(matrix)) -> #(
      Model(
        ..model,
        role_matrix: Some(matrix),
        matrix_status: load_status.Loaded,
      ),
      effect.none(),
      [shared.SetLoading(False)],
    )

    RoleMatrixLoaded(Error(err)) -> #(
      Model(
        ..model,
        matrix_status: load_status.Failed("Failed to load role matrix"),
      ),
      effect.none(),
      handle_error(err, "Failed to load role matrix"),
    )

    RetryLoadMatrix -> #(
      Model(..model, matrix_status: load_status.Loading),
      load_effect(admin_api.get_role_matrix, RoleMatrixLoaded),
      [],
    )

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

    UserRoleToggled(Error(err)) -> #(
      Model(..model, role_toggling: None),
      effect.none(),
      handle_error(err, "Failed to update role"),
    )

    OnlineUsersLoaded(Ok(ids)) -> #(
      Model(..model, online_user_ids: set.from_list(ids)),
      effect.none(),
      [],
    )

    // Presence is a non-critical overlay; ignore load errors silently
    // (stats/matrix loaders already surface auth failures).
    OnlineUsersLoaded(Error(_)) -> #(model, effect.none(), [])

    PresenceChanged(user_id, online) -> {
      let ids = case online {
        True -> set.insert(model.online_user_ids, user_id)
        False -> set.delete(model.online_user_ids, user_id)
      }
      #(Model(..model, online_user_ids: ids), effect.none(), [])
    }

    AddFilter(key, value) -> {
      let filters = dict.insert(model.active_filters, key, value)
      #(Model(..model, active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters)),
      ])
    }

    RemoveFilter(key) -> {
      let filters = dict.delete(model.active_filters, key)
      #(Model(..model, active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters)),
      ])
    }

    ClearFilters -> {
      // Clearing filters preserves the current sort selection — sorting
      // is independent from filtering (matches /records UX).
      let filters = record_filters.clear_user_filters(model.active_filters)
      #(Model(..model, active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters)),
      ])
    }

    ColumnHeaderClicked(col) -> {
      let #(cur_col, cur_dir) =
        table_sort.read_sort(
          model.active_filters,
          records_list.default_sort_col,
        )
      let #(new_col, new_dir) = table_sort.next_sort(cur_col, cur_dir, col)
      let new_filters =
        table_sort.write_sort(
          model.active_filters,
          new_col,
          new_dir,
          records_list.default_sort_col,
        )
      #(
        Model(..model, active_filters: new_filters),
        sync_filters_effect(new_filters),
        [shared.FetchBucket(bucket_key_for(new_filters))],
      )
    }
  }
}

// --- Helpers ---

fn handle_error(err: types.ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    types.AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

fn sync_filters_effect(filters: Dict(String, String)) -> Effect(Msg) {
  // No transient keys: every filter here has visible UI on the page.
  records_list_state.sync_filters_effect(
    filters,
    router.AdminDashboard,
    storage_key,
    [],
  )
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
    html.div([attribute.class("dashboard-content")], [
      stats_view(model),
      roles_section(model),
      records_section(model, shared),
    ]),
  ])
}

fn stats_view(model: Model) -> Element(Msg) {
  load_status.render(
    model.stats_status,
    fn() {
      html.div([attribute.class("loading")], [
        html.p([], [html.text("Loading statistics...")]),
      ])
    },
    fn() {
      case model.admin_stats {
        Some(stats) ->
          element.fragment([overview_section(stats), status_section(stats)])
        None ->
          html.div([attribute.class("loading")], [
            html.p([], [html.text("Loading statistics...")]),
          ])
      }
    },
    fn(msg) { retry_view(msg, RetryLoadStats) },
  )
}

fn retry_view(message: String, retry_msg: Msg) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
    html.button(
      [attribute.class("btn btn-primary"), event.on_click(retry_msg)],
      [html.text("Retry")],
    ),
  ])
}

fn overview_section(stats: models.AdminStats) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("System Overview")]),
    html.div([attribute.class("stats-grid")], [
      admin_stat_card(
        label: "Studies",
        count: stats.total_studies,
        color: "blue",
      ),
      admin_stat_card(
        label: "Records",
        count: stats.total_records,
        color: "green",
      ),
      admin_stat_card(label: "Users", count: stats.total_users, color: "purple"),
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
          admin_stat_card(
            label: s,
            count: count,
            color: status.color(status.from_backend_string(s)),
          )
        }),
    ),
  ])
}

fn roles_section(model: Model) -> Element(Msg) {
  html.div([attribute.class("dashboard-section")], [
    html.h3([], [html.text("Role Matrix")]),
    load_status.render(
      model.matrix_status,
      fn() {
        html.p([attribute.class("text-muted")], [
          html.text("Loading role matrix..."),
        ])
      },
      fn() {
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
                      html.tr([], [
                        html.th([], [html.text("User")]),
                        ..list.map(roles, fn(role) {
                          html.th([], [html.text(role)])
                        })
                      ]),
                    ]),
                    html.tbody(
                      [],
                      matrix.users
                        |> list.sort(fn(a, b) {
                          string.compare(a.email, b.email)
                        })
                        |> list.map(fn(user) {
                          role_matrix_row(model, user, roles)
                        }),
                    ),
                  ]),
                ])
            }
        }
      },
      fn(msg) { retry_view(msg, RetryLoadMatrix) },
    ),
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
      case set.contains(model.online_user_ids, user.id) {
        True -> html.span([attribute.class("online-dot")], [])
        False -> element.none()
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
  let key = bucket_key_for(model.active_filters)
  let records = cache.bucket_items(shared.cache, key)
  let status = cache.bucket_status(shared.cache, key)

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
    records_list.view(
      records,
      status,
      model.active_filters,
      shared,
      records_config(model, shared),
    ),
  ])
}

/// Shared-widget config for the admin records list. The status and
/// assigned-user cells keep the inline-edit affordances unique to admins;
/// the Actions column adds drill-in to the record detail. Patient is shown
/// as three columns (name / id / anon id) via `show_patient_columns`.
fn records_config(model: Model, shared: Shared) -> records_list.Config(Msg) {
  records_list.Config(
    show_type_filter: True,
    show_patient_filter: True,
    show_user_filter: True,
    show_patient_columns: True,
    show_study_series: False,
    show_modality: False,
    empty_message: shared.translate(i18n.AdminNoRecords),
    on_add_filter: AddFilter,
    on_remove_filter: RemoveFilter,
    on_clear_filters: ClearFilters,
    on_column_click: ColumnHeaderClicked,
    status_cell: fn(record) {
      status_cell(
        model: model,
        record_id: record_pk(record),
        status: record.status,
        translate: shared.translate,
      )
    },
    user_cell: Some(fn(record) {
      let rid = record_pk(record)
      assign_cell(
        shared: shared,
        record_id: rid,
        user_id: record.user_id,
        is_editing: model.editing_record_id == Some(rid),
      )
    }),
    // Drill-in: open the record detail. Kept alongside the inline
    // status/user controls rather than replacing them.
    actions_cell: fn(record) {
      records_list.detail_link(
        record,
        "btn btn-sm btn-outline",
        i18n.BtnView,
        shared.translate,
      )
    },
  )
}

fn record_pk(record: models.Record) -> Int {
  option.unwrap(record.id, 0)
}

fn assign_cell(
  shared shared: Shared,
  record_id record_id: Int,
  user_id user_id: Option(String),
  is_editing is_editing: Bool,
) -> Element(Msg) {
  case is_editing {
    True ->
      html.div([attribute.class("assign-cell")], [
        user_dropdown(shared, record_id, user_id),
      ])
    False ->
      case user_id {
        Some(uid) -> {
          let email = cache.user_email(shared.cache, uid)
          html.div([attribute.class("assign-cell")], [
            html.span([attribute.class("assign-email")], [html.text(email)]),
            html.button(
              [
                attribute.class("btn btn-sm btn-outline"),
                event.on_click(ToggleAssignDropdown(Some(record_id))),
              ],
              [html.text(shared.translate(i18n.BtnChange))],
            ),
          ])
        }
        None ->
          html.button(
            [
              attribute.class("btn btn-sm btn-primary"),
              event.on_click(ToggleAssignDropdown(Some(record_id))),
            ],
            [html.text(shared.translate(i18n.BtnAssign))],
          )
      }
  }
}

fn user_dropdown(
  shared: Shared,
  record_id: Int,
  user_id: Option(String),
) -> Element(Msg) {
  let unassign_option = case user_id {
    Some(_) -> [
      html.option(
        [attribute.value("__unassign__")],
        shared.translate(i18n.AdminOptionUnassign),
      ),
    ]
    None -> []
  }
  let user_options =
    dict.values(shared.cache.users)
    |> list.sort(fn(a, b) { string.compare(a.email, b.email) })
    |> list.map(fn(user) { html.option([attribute.value(user.id)], user.email) })
  html.div([attribute.class("assign-dropdown")], [
    html.select(
      [
        attribute.class("form-select form-select-sm"),
        event.on_input(fn(value) {
          case value {
            "" -> ToggleAssignDropdown(None)
            "__unassign__" -> UnassignUser(record_id)
            uid -> AssignUser(record_id, uid)
          }
        }),
      ],
      list.flatten([
        [
          html.option(
            [attribute.value("")],
            shared.translate(i18n.AdminSelectUser),
          ),
        ],
        unassign_option,
        user_options,
      ]),
    ),
    html.button(
      [
        attribute.class("btn btn-sm btn-outline"),
        event.on_click(ToggleAssignDropdown(None)),
      ],
      [html.text(shared.translate(i18n.BtnCancel))],
    ),
  ])
}

fn status_cell(
  model model: Model,
  record_id record_id: Int,
  status record_status: types.RecordStatus,
  translate translate: fn(Key) -> String,
) -> Element(Msg) {
  let is_editing = model.editing_status_record_id == Some(record_id)
  case is_editing {
    True ->
      html.div([attribute.class("assign-cell")], [
        status_dropdown(record_id, translate),
      ])
    False ->
      html.div([attribute.class("assign-cell")], [
        status_badge.render(record_status, translate),
        html.text(" "),
        html.button(
          [
            attribute.class("btn btn-sm btn-outline"),
            event.on_click(ToggleStatusDropdown(Some(record_id))),
          ],
          [html.text(translate(i18n.BtnChange))],
        ),
      ])
  }
}

fn status_dropdown(record_id: Int, translate: fn(Key) -> String) -> Element(Msg) {
  let statuses = status.all_statuses()
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
        html.option([attribute.value("")], translate(i18n.AdminSelectStatus)),
        ..list.map(statuses, fn(s) {
          html.option(
            [attribute.value(status.to_backend_string(s))],
            translate(status.to_i18n_key(s)),
          )
        })
      ],
    ),
    html.button(
      [
        attribute.class("btn btn-sm btn-outline"),
        event.on_click(ToggleStatusDropdown(None)),
      ],
      [html.text(translate(i18n.BtnCancel))],
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
