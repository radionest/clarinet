// Records list page — self-contained MVU module
import api/models.{type Record}
import api/records
import api/types.{type ApiError, AuthError}
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

// --- Model ---

pub type Model {
  Model(active_filters: Dict(String, String))
}

// --- Msg ---

pub type Msg {
  AddFilter(key: String, value: String)
  RemoveFilter(key: String)
  ClearFilters
  RequestFail(record_id: String)
  Restart(record_id: String)
  RestartResult(Result(Record, ApiError))
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(Model(active_filters: dict.new()), effect.none(), [shared.ReloadRecords])
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
      #(Model(active_filters: filters), effect.none(), [])
    }

    RemoveFilter(key) -> {
      let filters = dict.delete(model.active_filters, key)
      #(Model(active_filters: filters), effect.none(), [])
    }

    ClearFilters -> #(Model(active_filters: dict.new()), effect.none(), [])

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
      shared.ReloadRecords,
    ])

    RestartResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, shared.translate(i18n.RecordsMsgRestartFailed)),
    )
  }
}

// --- Helpers ---

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
      let all_records = dict.values(shared.cache.records)
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
  let records =
    record_filters.apply_filters(all_records, model.active_filters)
    |> list.sort(fn(a, b) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    })

  case records {
    [] ->
      html.p([attribute.class("text-muted")], [html.text(shared.translate(i18n.RecordsNoFound))])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text(shared.translate(i18n.ThId))]),
              html.th([], [html.text(shared.translate(i18n.ThRecordType))]),
              html.th([], [html.text(shared.translate(i18n.ThStatus))]),
              html.th([], [html.text(shared.translate(i18n.ThPatient))]),
              html.th([], [html.text(shared.translate(i18n.ThStudySeries))]),
              html.th([], [html.text(shared.translate(i18n.ThModality))]),
              html.th([], [html.text(shared.translate(i18n.ThActions))]),
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
    html.td([], [
      html.text(case record.series {
        Some(series) -> option.unwrap(series.modality, "-")
        None ->
          case record.study {
            Some(study) -> option.unwrap(study.modalities_in_study, "-")
            None -> "-"
          }
      }),
    ]),
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
