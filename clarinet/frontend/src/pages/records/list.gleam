// Records list page — self-contained MVU module
import api/models.{type Record, type User}
import api/records
import api/types.{type ApiError, AuthError}
import cache
import cache/bucket
import clarinet_frontend/i18n
import components/forms/base
import components/status_badge
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/permissions
import utils/record_filters
import utils/records_list_state
import utils/records_query
import utils/table_sort

// --- Model ---

pub type Model {
  Model(active_filters: Dict(String, String))
}

// --- Msg ---

pub type Msg {
  AddFilter(key: String, value: String)
  RemoveFilter(key: String)
  ClearFilters
  ColumnHeaderClicked(column: String)
  RequestFail(record_id: String)
  Restart(record_id: String)
  RestartResult(Result(Record, ApiError))
}

const default_sort_col = "id"

// --- Init ---

const storage_key = "records.filters"

pub fn init(
  filters: Dict(String, String),
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let #(effective_filters, init_fx) =
    records_list_state.resolve_initial_filters(
      filters,
      storage_key,
      router.Records,
    )
  let key = bucket_key_for(effective_filters, shared.user)
  // The assigned-user column resolves names from `shared.cache.users`,
  // populated by the admin-only `GET /user/`. Load it for admins only so
  // regular users don't trigger a 403 on every visit.
  let base_out = [shared.FetchBucket(key), shared.ReloadFilterOptions]
  let out_msgs = case is_admin(shared.user) {
    True -> [shared.ReloadUsers, ..base_out]
    False -> base_out
  }
  #(Model(active_filters: effective_filters), init_fx, out_msgs)
}

/// Bucket key for the records list. Non-admins see only their own records
/// (the historical `RecordsMine(uid)` scope), admins see all records.
fn bucket_key_for(
  filters: Dict(String, String),
  user: option.Option(User),
) -> bucket.BucketKey {
  let base = records_query.from_filters(filters)
  let scoped = case user {
    Some(u) ->
      case permissions.is_admin_user(u) {
        True -> base
        False -> records_query.with_user_scope(base, u.id)
      }
    None -> base
  }
  bucket.Records(scoped)
}

/// The assigned-user column is admin-only: `shared.cache.users` is
/// populated by the admin-only `GET /user/`.
fn is_admin(user: option.Option(User)) -> Bool {
  case user {
    Some(u) -> permissions.is_admin_user(u)
    None -> False
  }
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    AddFilter(key, value) -> {
      let filters = dict.insert(model.active_filters, key, value)
      #(Model(active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters, shared.user)),
      ])
    }

    RemoveFilter(key) -> {
      let filters = dict.delete(model.active_filters, key)
      #(Model(active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters, shared.user)),
      ])
    }

    ClearFilters -> {
      // Clearing filters preserves the current sort selection — sorting
      // is independent from filtering and resetting it on every "Clear"
      // would be surprising.
      let filters = record_filters.clear_user_filters(model.active_filters)
      #(Model(active_filters: filters), sync_filters_effect(filters), [
        shared.FetchBucket(bucket_key_for(filters, shared.user)),
      ])
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
      #(Model(active_filters: new_filters), sync_filters_effect(new_filters), [
        shared.FetchBucket(bucket_key_for(new_filters, shared.user)),
      ])
    }

    RequestFail(record_id) -> #(model, effect.none(), [
      shared.OpenFailPrompt(record_id),
    ])

    Restart(record_id) -> {
      let eff = {
        use dispatch <- effect.from
        records.restart_record(record_id)
        |> promise.tap(fn(result) { dispatch(RestartResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    RestartResult(Ok(record)) -> #(model, effect.none(), [
      shared.SetLoading(False),
      shared.CacheRecord(record),
      shared.ShowSuccess(shared.translate(i18n.RecordsMsgRestarted)),
      shared.InvalidateAllRecordBuckets,
    ])

    RestartResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, shared.translate(i18n.RecordsMsgRestartFailed)),
    )
  }
}

// --- Helpers ---

fn sync_filters_effect(filters: Dict(String, String)) -> Effect(Msg) {
  records_list_state.sync_filters_effect(filters, router.Records, storage_key)
}

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let t = shared.translate
  let title = case is_admin(shared.user) {
    True -> t(i18n.RecordsAllTitle)
    False -> t(i18n.RecordsTitle)
  }

  let key = bucket_key_for(model.active_filters, shared.user)
  let records = cache.bucket_items(shared.cache, key)
  let status = cache.bucket_status(shared.cache, key)

  let body = case status {
    bucket.Cold | bucket.Loading ->
      html.div([attribute.class("loading-indicator")], [
        html.text(shared.translate(i18n.LblLoading)),
      ])
    bucket.Failed(msg) ->
      html.p([attribute.class("text-error")], [html.text(msg)])
    _ -> records_table(model, shared, records)
  }

  html.div([attribute.class("container")], [
    html.h1([], [html.text(title)]),
    html.div([], [filter_bar(model, shared), body]),
  ])
}

fn filter_bar(model: Model, shared: Shared) -> Element(Msg) {
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

  let #(patient_values, type_values) = case shared.cache.filter_options {
    Some(opts) -> #(opts.patients, opts.record_types)
    None -> #([], [])
  }

  let status_options = record_filters.status_options(shared.translate)
  let type_options = record_filters.type_options(type_values, shared.translate)
  let patient_options =
    record_filters.patient_options(patient_values, shared.translate)

  let has_filters = record_filters.has_user_filters(model.active_filters)

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
    case has_filters {
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
  records: List(Record),
) -> Element(Msg) {
  // Filtering and sorting are server-side via the bucket key. The
  // (sort_col, sort_dir) pair is read only to render the arrow indicators
  // on column headers.
  let #(sort_col, sort_dir) =
    table_sort.read_sort(model.active_filters, default_sort_col)
  let show_user = is_admin(shared.user)

  case records {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(shared.translate(i18n.RecordsNoFound)),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr(
              [],
              list.flatten([
                [
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
                  table_sort.th_static(shared.translate(i18n.ThStudySeries)),
                  table_sort.th_sortable(
                    shared.translate(i18n.ThModality),
                    "modality",
                    sort_col,
                    sort_dir,
                    ColumnHeaderClicked,
                  ),
                ],
                case show_user {
                  True -> [
                    table_sort.th_sortable(
                      shared.translate(i18n.ThAssignedUser),
                      "user",
                      sort_col,
                      sort_dir,
                      ColumnHeaderClicked,
                    ),
                  ]
                  False -> []
                },
                [table_sort.th_static(shared.translate(i18n.ThActions))],
              ]),
            ),
          ]),
          html.tbody(
            [],
            list.map(records, fn(record) {
              record_row(shared, show_user, record)
            }),
          ),
        ]),
      ])
  }
}

/// Resolve the modality column value: prefer series.modality, fall back
/// to study.modalities_in_study, then to a single dash. The same string
/// is used for display and as the sort key, so rows without a modality
/// cluster together consistently in both directions.
fn record_modality_text(record: Record) -> String {
  let raw = case record.series {
    Some(series) -> series.modality
    None ->
      case record.study {
        Some(study) -> study.modalities_in_study
        None -> None
      }
  }
  option.unwrap(raw, "-")
}

/// Resolve the assigned-user cell: email from the admin-loaded users
/// cache, falling back to the raw id, or a dash when unassigned.
fn user_cell_content(shared: Shared, record: Record) -> Element(Msg) {
  case record.user_id {
    Some(uid) -> html.text(cache.user_email(shared.cache, uid))
    None -> html.text("—")
  }
}

fn record_row(shared: Shared, show_user: Bool, record: Record) -> Element(Msg) {
  let record_id = option.unwrap(record.id, 0)
  let record_id_str = int.to_string(record_id)

  let type_label = case record.record_type {
    Some(rt) -> option.unwrap(rt.label, rt.name)
    None -> record.record_type_name
  }

  let can_fill = permissions.can_fill_record(record, shared.user)
  let can_edit = permissions.can_edit_record(record, shared.user)
  let can_fail = permissions.can_fail_record(record, shared.user)
  let can_restart = permissions.can_restart_record(record, shared.user)

  html.tr(
    [],
    list.flatten([
      [
        html.td([], [html.text(record_id_str)]),
        html.td([], [html.text(type_label)]),
        html.td([], [status_badge.render(record.status, shared.translate)]),
        html.td([], [html.text(record.patient_id)]),
        html.td([], [html.text(format_study_series_summary(record))]),
        html.td([], [html.text(record_modality_text(record))]),
      ],
      case show_user {
        True -> [html.td([], [user_cell_content(shared, record)])]
        False -> []
      },
      [
        html.td([], [
          case can_fill, can_edit {
            True, _ ->
              html.a(
                [
                  attribute.href(
                    router.route_to_path(router.RecordDetail(record_id_str)),
                  ),
                  attribute.class("btn btn-sm btn-primary"),
                ],
                [html.text(shared.translate(i18n.BtnFill))],
              )
            _, True ->
              html.a(
                [
                  attribute.href(
                    router.route_to_path(router.RecordDetail(record_id_str)),
                  ),
                  attribute.class("btn btn-sm btn-secondary"),
                ],
                [html.text(shared.translate(i18n.BtnEdit))],
              )
            _, _ ->
              html.a(
                [
                  attribute.href(
                    router.route_to_path(router.RecordDetail(record_id_str)),
                  ),
                  attribute.class("btn btn-sm btn-outline"),
                ],
                [html.text(shared.translate(i18n.BtnView))],
              )
          },
          case can_fail {
            True ->
              html.button(
                [
                  attribute.class("btn btn-sm btn-danger"),
                  event.on_click(RequestFail(record_id_str)),
                ],
                [html.text(shared.translate(i18n.BtnFail))],
              )
            False -> element.none()
          },
          case can_restart {
            True ->
              html.button(
                [
                  attribute.class("btn btn-sm btn-warning"),
                  event.on_click(Restart(record_id_str)),
                ],
                [html.text(shared.translate(i18n.BtnRestart))],
              )
            False -> element.none()
          },
        ]),
      ],
    ]),
  )
}

fn format_study_series_summary(record: Record) -> String {
  let study_part = case record.study {
    Some(study) -> option.unwrap(study.study_description, "-")
    None -> "-"
  }

  let series_part = case record.series {
    Some(series) -> {
      let label = case series.modality, series.series_description {
        Some(m), Some(d) -> m <> " - " <> d
        Some(m), None -> m
        None, Some(d) -> d
        None, None -> "-"
      }
      case series.instance_count {
        Some(n) -> label <> " (" <> int.to_string(n) <> " img)"
        None -> label
      }
    }
    None -> "-"
  }

  case study_part, series_part {
    "-", "-" -> "-"
    s, "-" -> s
    "-", sr -> sr
    s, sr -> s <> " / " <> sr
  }
}
