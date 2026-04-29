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
import gleam/order
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/permissions
import utils/record_filters
import utils/status
import utils/storage
import utils/table_sort.{type SortDirection}
import utils/url

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
  let key = bucket_key_for_user(shared.user)
  let #(effective_filters, init_fx) = case dict.is_empty(filters) {
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
  #(Model(active_filters: effective_filters), init_fx, [shared.FetchBucket(key)])
}

fn bucket_key_for_user(user: option.Option(User)) -> bucket.BucketKey {
  case user {
    Some(u) if u.is_superuser -> bucket.RecordsAll
    Some(u) -> bucket.RecordsMine(u.id)
    None -> bucket.RecordsAll
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
      #(Model(active_filters: filters), sync_filters_effect(filters), [])
    }

    RemoveFilter(key) -> {
      let filters = dict.delete(model.active_filters, key)
      #(Model(active_filters: filters), sync_filters_effect(filters), [])
    }

    ClearFilters -> {
      // Clearing filters preserves the current sort selection — sorting
      // is independent from filtering and resetting it on every "Clear"
      // would be surprising.
      let filters = record_filters.clear_user_filters(model.active_filters)
      #(Model(active_filters: filters), sync_filters_effect(filters), [])
    }

    ColumnHeaderClicked(col) -> {
      let #(cur_col, cur_dir) =
        table_sort.read_sort(model.active_filters, default_sort_col)
      let #(new_col, new_dir) = table_sort.next_sort(cur_col, cur_dir, col)
      let new_filters =
        table_sort.write_sort(model.active_filters, new_col, new_dir, default_sort_col)
      #(Model(active_filters: new_filters), sync_filters_effect(new_filters), [])
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

fn sync_url_effect(filters: Dict(String, String)) -> Effect(Msg) {
  url.replace_route(router.Records(filters))
}

fn save_filters(filters: Dict(String, String)) -> Effect(Msg) {
  storage.save_dict(storage.Local, storage_key, filters)
}

fn sync_filters_effect(filters: Dict(String, String)) -> Effect(Msg) {
  effect.batch([sync_url_effect(filters), save_filters(filters)])
}

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let is_admin = case shared.user {
    Some(u) -> u.is_superuser
    None -> False
  }

  let t = shared.translate
  let title = case is_admin {
    True -> t(i18n.RecordsAllTitle)
    False -> t(i18n.RecordsTitle)
  }

  html.div([attribute.class("container")], [
    html.h1([], [html.text(title)]),
    {
      let key = bucket_key_for_user(shared.user)
      let all_records = cache.bucket_items(shared.cache, key)
      html.div([], [
        filter_bar(model, shared, all_records),
        records_table(model, shared, all_records),
      ])
    },
  ])
}

fn filter_bar(model: Model, shared: Shared, all_records: List(Record)) -> Element(Msg) {
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

  let status_options = record_filters.status_options(shared.translate)
  let type_options = record_filters.type_options(all_records, shared.translate)
  let patient_options = record_filters.patient_options(all_records, shared.translate)

  let has_filters = !dict.is_empty(model.active_filters)

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
  all_records: List(Record),
) -> Element(Msg) {
  let #(sort_col, sort_dir) =
    table_sort.read_sort(model.active_filters, default_sort_col)
  let cmp = record_comparator(sort_col, sort_dir)
  let records =
    record_filters.apply_filters(all_records, model.active_filters)
    |> list.sort(cmp)

  case records {
    [] ->
      html.p([attribute.class("text-muted")], [html.text(shared.translate(i18n.RecordsNoFound))])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              table_sort.th_sortable(shared.translate(i18n.ThId), "id", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(shared.translate(i18n.ThRecordType), "record_type", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(shared.translate(i18n.ThStatus), "status", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(shared.translate(i18n.ThPatient), "patient", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_static(shared.translate(i18n.ThStudySeries)),
              table_sort.th_sortable(shared.translate(i18n.ThModality), "modality", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_static(shared.translate(i18n.ThActions)),
            ]),
          ]),
          html.tbody(
            [],
            list.map(records, fn(record) { record_row(shared, record) }),
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

fn record_comparator(
  col: String,
  dir: SortDirection,
) -> fn(Record, Record) -> order.Order {
  let base = case col {
    "id" -> fn(a: Record, b: Record) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    }
    "record_type" -> fn(a: Record, b: Record) {
      string.compare(a.record_type_name, b.record_type_name)
    }
    "status" -> fn(a: Record, b: Record) {
      string.compare(
        status.to_backend_string(a.status),
        status.to_backend_string(b.status),
      )
    }
    "patient" -> fn(a: Record, b: Record) {
      string.compare(a.patient_id, b.patient_id)
    }
    "modality" -> fn(a: Record, b: Record) {
      string.compare(record_modality_text(a), record_modality_text(b))
    }
    _ -> fn(a: Record, b: Record) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    }
  }
  table_sort.with_direction(base, dir)
}

fn record_row(shared: Shared, record: Record) -> Element(Msg) {
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

  html.tr([], [
    html.td([], [html.text(record_id_str)]),
    html.td([], [html.text(type_label)]),
    html.td([], [status_badge.render(record.status, shared.translate)]),
    html.td([], [html.text(record.patient_id)]),
    html.td([], [html.text(format_study_series_summary(record))]),
    html.td([], [html.text(record_modality_text(record))]),
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
  ])
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
