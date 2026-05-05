// Admin Dashboard page — self-contained MVU module
import api/admin as admin_api
import api/models
import api/types
import cache
import cache/bucket
import clarinet_frontend/i18n.{type Key}
import components/forms/base
import components/status_badge
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/order
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
import utils/status
import utils/storage
import utils/table_sort.{type SortDirection}
import utils/url

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
  // Status change
  ToggleStatusDropdown(record_id: Option(Int))
  ChangeStatus(record_id: Int, status: String)
  ChangeStatusResult(Result(models.Record, types.ApiError))
  // Role matrix
  RoleMatrixLoaded(Result(models.RoleMatrix, types.ApiError))
  RetryLoadMatrix
  ToggleUserRole(user_id: String, role_name: String, add: Bool)
  UserRoleToggled(Result(Nil, types.ApiError))
  // Records filters / sort
  AddFilter(key: String, value: String)
  RemoveFilter(key: String)
  ClearFilters
  ColumnHeaderClicked(column: String)
}

const default_sort_col = "id"

const storage_key = "admin.records.filters"

// --- Init ---

pub fn init(
  filters: Dict(String, String),
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let #(effective_filters, filters_fx) = case dict.is_empty(filters) {
    // URL has filters — use them and persist to localStorage
    False -> #(filters, save_filters(filters))
    // URL empty — try localStorage fallback
    True -> {
      let saved = storage.load_dict_sync(storage.Local, storage_key)
      case dict.is_empty(saved) {
        True -> #(dict.new(), effect.none())
        // Restore from localStorage and sync URL
        False -> #(saved, sync_url_effect(saved))
      }
    }
  }

  let model =
    Model(
      admin_stats: None,
      stats_status: load_status.Loading,
      editing_record_id: None,
      editing_status_record_id: None,
      role_matrix: None,
      matrix_status: load_status.Loading,
      role_toggling: None,
      active_filters: effective_filters,
    )
  let effects =
    effect.batch([
      load_effect(admin_api.get_admin_stats, AdminStatsLoaded),
      load_effect(admin_api.get_role_matrix, RoleMatrixLoaded),
      filters_fx,
    ])
  #(model, effects, [shared.FetchBucket(bucket.RecordsAll), shared.ReloadUsers])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    AdminStatsLoaded(Ok(stats)) ->
      #(
        Model(
          ..model,
          admin_stats: Some(stats),
          stats_status: load_status.Loaded,
        ),
        effect.none(),
        [shared.SetLoading(False)],
      )

    AdminStatsLoaded(Error(err)) ->
      #(
        Model(
          ..model,
          stats_status: load_status.Failed("Failed to load admin statistics"),
        ),
        effect.none(),
        handle_error(err, "Failed to load admin statistics"),
      )

    RetryLoadStats ->
      #(
        Model(..model, stats_status: load_status.Loading),
        load_effect(admin_api.get_admin_stats, AdminStatsLoaded),
        [],
      )

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

    AssignUserResult(Ok(record)) -> {
      // Refresh admin stats — assigning a user can change `unassigned_records`
      // and similar derived counts that the cards display.
      let stats_eff = load_effect(admin_api.get_admin_stats, AdminStatsLoaded)
      #(
        Model(..model, stats_status: load_status.Loading),
        stats_eff,
        [
          shared.SetLoading(False),
          shared.CacheRecord(record),
          shared.ShowSuccess("User assigned successfully"),
        ],
      )
    }

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

    ChangeStatusResult(Ok(record)) -> {
      // Refresh admin stats — `records_by_status` cards become stale otherwise.
      let stats_eff = load_effect(admin_api.get_admin_stats, AdminStatsLoaded)
      #(
        Model(..model, stats_status: load_status.Loading),
        stats_eff,
        [
          shared.SetLoading(False),
          shared.CacheRecord(record),
          shared.ShowSuccess("Status updated successfully"),
        ],
      )
    }

    ChangeStatusResult(Error(err)) ->
      #(
        model,
        effect.none(),
        handle_error(err, "Failed to update record status"),
      )

    RoleMatrixLoaded(Ok(matrix)) ->
      #(
        Model(
          ..model,
          role_matrix: Some(matrix),
          matrix_status: load_status.Loaded,
        ),
        effect.none(),
        [shared.SetLoading(False)],
      )

    RoleMatrixLoaded(Error(err)) ->
      #(
        Model(
          ..model,
          matrix_status: load_status.Failed("Failed to load role matrix"),
        ),
        effect.none(),
        handle_error(err, "Failed to load role matrix"),
      )

    RetryLoadMatrix ->
      #(
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

    UserRoleToggled(Error(err)) ->
      #(
        Model(..model, role_toggling: None),
        effect.none(),
        handle_error(err, "Failed to update role"),
      )

    AddFilter(key, value) -> {
      let filters = dict.insert(model.active_filters, key, value)
      #(Model(..model, active_filters: filters), sync_filters_effect(filters), [])
    }

    RemoveFilter(key) -> {
      let filters = dict.delete(model.active_filters, key)
      #(Model(..model, active_filters: filters), sync_filters_effect(filters), [])
    }

    ClearFilters -> {
      // Clearing filters preserves the current sort selection — sorting
      // is independent from filtering (matches /records UX).
      let filters = record_filters.clear_user_filters(model.active_filters)
      #(Model(..model, active_filters: filters), sync_filters_effect(filters), [])
    }

    ColumnHeaderClicked(col) -> {
      let #(cur_col, cur_dir) =
        table_sort.read_sort(model.active_filters, default_sort_col)
      let #(new_col, new_dir) = table_sort.next_sort(cur_col, cur_dir, col)
      let new_filters =
        table_sort.write_sort(
          model.active_filters,
          new_col,
          new_dir,
          default_sort_col,
        )
      #(Model(..model, active_filters: new_filters), sync_filters_effect(new_filters), [])
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

fn sync_url_effect(filters: Dict(String, String)) -> Effect(Msg) {
  url.replace_route(router.AdminDashboard(filters))
}

fn save_filters(filters: Dict(String, String)) -> Effect(Msg) {
  storage.save_dict(storage.Local, storage_key, filters)
}

fn sync_filters_effect(filters: Dict(String, String)) -> Effect(Msg) {
  effect.batch([sync_url_effect(filters), save_filters(filters)])
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
  let all_records = cache.bucket_items(shared.cache, bucket.RecordsAll)

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
    filter_bar(model, shared, all_records),
    records_table(model, shared, all_records),
  ])
}

fn filter_bar(
  model: Model,
  shared: Shared,
  all_records: List(models.Record),
) -> Element(Msg) {
  let status_value =
    dict.get(model.active_filters, "status")
    |> option.from_result()
    |> option.unwrap("")

  let type_value =
    dict.get(model.active_filters, "record_type")
    |> option.from_result()
    |> option.unwrap("")

  let patient_value =
    dict.get(model.active_filters, "patient")
    |> option.from_result()
    |> option.unwrap("")

  let user_value =
    dict.get(model.active_filters, "user")
    |> option.from_result()
    |> option.unwrap("")

  let status_options = record_filters.status_options(shared.translate)
  let type_options = record_filters.type_options(all_records, shared.translate)
  let patient_options =
    record_filters.patient_options(all_records, shared.translate)
  let user_options =
    record_filters.user_options(all_records, shared.cache.users, shared.translate)

  let has_user_filters =
    list.any(record_filters.user_filter_keys, fn(key) {
      dict.has_key(model.active_filters, key)
    })

  html.div([attribute.class("filter-bar")], [
    base.select(
      name: "filter-status",
      value: status_value,
      options: status_options,
      on_change: fn(val) {
        case val {
          "" -> RemoveFilter("status")
          _ -> AddFilter("status", val)
        }
      },
    ),
    base.select(
      name: "filter-record-type",
      value: type_value,
      options: type_options,
      on_change: fn(val) {
        case val {
          "" -> RemoveFilter("record_type")
          _ -> AddFilter("record_type", val)
        }
      },
    ),
    base.select(
      name: "filter-patient",
      value: patient_value,
      options: patient_options,
      on_change: fn(val) {
        case val {
          "" -> RemoveFilter("patient")
          _ -> AddFilter("patient", val)
        }
      },
    ),
    base.select(
      name: "filter-user",
      value: user_value,
      options: user_options,
      on_change: fn(val) {
        case val {
          "" -> RemoveFilter("user")
          _ -> AddFilter("user", val)
        }
      },
    ),
    case has_user_filters {
      True ->
        html.button(
          [
            attribute.type_("button"),
            attribute.class("btn btn-sm btn-outline"),
            event.on_click(ClearFilters),
          ],
          [html.text(shared.translate(i18n.BtnClearFilters))],
        )
      False -> html.text("")
    },
  ])
}

fn records_table(
  model: Model,
  shared: Shared,
  all_records: List(models.Record),
) -> Element(Msg) {
  let #(sort_col, sort_dir) =
    table_sort.read_sort(model.active_filters, default_sort_col)
  let cmp = record_comparator(sort_col, sort_dir, shared.cache.users)
  let records =
    record_filters.apply_filters(all_records, model.active_filters)
    |> list.sort(cmp)

  case records {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(shared.translate(i18n.AdminNoRecords)),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              table_sort.th_sortable(
                shared.translate(i18n.ThId),
                "id",
                sort_col,
                sort_dir,
                ColumnHeaderClicked,
              ),
              table_sort.th_sortable(
                shared.translate(i18n.ThRecordType),
                "record_type",
                sort_col,
                sort_dir,
                ColumnHeaderClicked,
              ),
              table_sort.th_sortable(
                shared.translate(i18n.ThStatus),
                "status",
                sort_col,
                sort_dir,
                ColumnHeaderClicked,
              ),
              table_sort.th_sortable(
                shared.translate(i18n.ThPatient),
                "patient",
                sort_col,
                sort_dir,
                ColumnHeaderClicked,
              ),
              table_sort.th_sortable(
                shared.translate(i18n.ThAssignedUser),
                "user",
                sort_col,
                sort_dir,
                ColumnHeaderClicked,
              ),
            ]),
          ]),
          html.tbody(
            [],
            list.map(records, fn(record) { record_row(model, shared, record) }),
          ),
        ]),
      ])
  }
}

fn user_email(
  user_id: Option(String),
  users: Dict(String, models.User),
) -> String {
  case user_id {
    Some(uid) ->
      case dict.get(users, uid) {
        Ok(user) -> user.email
        Error(_) -> uid
      }
    None -> ""
  }
}

fn record_comparator(
  col: String,
  dir: SortDirection,
  users: Dict(String, models.User),
) -> fn(models.Record, models.Record) -> order.Order {
  let base = case col {
    "id" -> fn(a: models.Record, b: models.Record) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    }
    "record_type" -> fn(a: models.Record, b: models.Record) {
      string.compare(a.record_type_name, b.record_type_name)
    }
    "status" -> fn(a: models.Record, b: models.Record) {
      string.compare(
        status.to_backend_string(a.status),
        status.to_backend_string(b.status),
      )
    }
    "patient" -> fn(a: models.Record, b: models.Record) {
      string.compare(a.patient_id, b.patient_id)
    }
    "user" -> fn(a: models.Record, b: models.Record) {
      string.compare(user_email(a.user_id, users), user_email(b.user_id, users))
    }
    _ -> fn(a: models.Record, b: models.Record) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    }
  }
  table_sort.with_direction(base, dir)
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
        translate: shared.translate,
      ),
    ]),
    html.td([], [
      html.text(case record.patient {
        Some(patient) ->
          case patient.name {
            Some(name) -> name <> " (" <> record.patient_id <> ")"
            None -> record.patient_id
          }
        None -> record.patient_id
      }),
    ]),
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
    True ->
      html.div([attribute.class("assign-cell")], [
        user_dropdown(shared, record_id),
      ])
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

fn status_dropdown(
  record_id: Int,
  translate: fn(Key) -> String,
) -> Element(Msg) {
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

fn status_color(s: String) -> String {
  case s {
    "blocked" -> "yellow"
    "pending" -> "blue"
    "inwork" -> "orange"
    "finished" -> "green"
    "failed" -> "red"
    "paused" -> "gray"
    _ -> "blue"
  }
}
