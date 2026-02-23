// Records list page
import api/models.{type Record}
import api/types
import utils/permissions
import components/forms/base
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  let is_admin = case model.user {
    Some(u) -> u.is_superuser
    None -> False
  }

  let title = case is_admin {
    True -> "All Records"
    False -> "My Records"
  }

  html.div([attribute.class("container")], [
    html.h1([], [html.text(title)]),
    case model.loading {
      True ->
        html.div([attribute.class("loading")], [
          html.p([], [html.text("Loading records...")]),
        ])
      False -> {
        let all_records = dict.values(model.records)
        html.div([], [
          filter_bar(model, all_records),
          records_table(model, all_records),
        ])
      }
    },
  ])
}

fn filter_bar(model: Model, all_records: List(Record)) -> Element(Msg) {
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

  let status_options = [
    #("", "All Statuses"),
    #("pending", "Pending"),
    #("inwork", "In Progress"),
    #("finished", "Completed"),
    #("failed", "Failed"),
    #("paused", "Paused"),
  ]

  let type_options = {
    let types =
      list.map(all_records, fn(r) { r.record_type_name })
      |> list.unique()
      |> list.sort(fn(a, b) { string.compare(a, b) })
    [#("", "All Types"), ..list.map(types, fn(t) { #(t, t) })]
  }

  let patient_options = {
    let patients =
      list.map(all_records, fn(r) { r.patient_id })
      |> list.unique()
      |> list.sort(fn(a, b) { string.compare(a, b) })
    [#("", "All Patients"), ..list.map(patients, fn(p) { #(p, p) })]
  }

  let has_filters = !dict.is_empty(model.active_filters)

  html.div([attribute.class("filter-bar")], [
    base.select(
      name: "filter-status",
      value: status_value,
      options: status_options,
      on_change: fn(val) {
        case val {
          "" -> store.RemoveFilter("status")
          _ -> store.AddFilter("status", val)
        }
      },
    ),
    base.select(
      name: "filter-record-type",
      value: type_value,
      options: type_options,
      on_change: fn(val) {
        case val {
          "" -> store.RemoveFilter("record_type")
          _ -> store.AddFilter("record_type", val)
        }
      },
    ),
    base.select(
      name: "filter-patient",
      value: patient_value,
      options: patient_options,
      on_change: fn(val) {
        case val {
          "" -> store.RemoveFilter("patient")
          _ -> store.AddFilter("patient", val)
        }
      },
    ),
    case has_filters {
      True ->
        html.button(
          [
            attribute.type_("button"),
            attribute.class("btn btn-sm btn-outline"),
            event.on_click(store.ClearFilters),
          ],
          [html.text("Clear Filters")],
        )
      False -> html.text("")
    },
  ])
}

fn status_to_string(status: types.RecordStatus) -> String {
  case status {
    types.Pending -> "pending"
    types.InWork -> "inwork"
    types.Finished -> "finished"
    types.Failed -> "failed"
    types.Paused -> "paused"
  }
}

fn apply_filters(
  records: List(Record),
  filters: dict.Dict(String, String),
) -> List(Record) {
  list.filter(records, fn(record) {
    let status_ok = case dict.get(filters, "status") {
      Ok(status_filter) -> status_to_string(record.status) == status_filter
      Error(_) -> True
    }

    let type_ok = case dict.get(filters, "record_type") {
      Ok(type_filter) -> record.record_type_name == type_filter
      Error(_) -> True
    }

    let patient_ok = case dict.get(filters, "patient") {
      Ok(patient_filter) -> record.patient_id == patient_filter
      Error(_) -> True
    }

    status_ok && type_ok && patient_ok
  })
}

fn records_table(model: Model, all_records: List(Record)) -> Element(Msg) {
  let records =
    apply_filters(all_records, model.active_filters)
    |> list.sort(fn(a, b) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    })

  case records {
    [] ->
      html.p([attribute.class("text-muted")], [html.text("No records found.")])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text("ID")]),
              html.th([], [html.text("Record Type")]),
              html.th([], [html.text("Status")]),
              html.th([], [html.text("Patient")]),
              html.th([], [html.text("Actions")]),
            ]),
          ]),
          html.tbody(
            [],
            list.map(records, fn(record) { record_row(model, record) }),
          ),
        ]),
      ])
  }
}

fn record_row(model: Model, record: Record) -> Element(Msg) {
  let record_id = option.unwrap(record.id, 0)
  let record_id_str = int.to_string(record_id)

  let type_label = case record.record_type {
    Some(rt) -> option.unwrap(rt.label, rt.name)
    None -> record.record_type_name
  }

  let #(status_class, status_text) = case record.status {
    types.Pending -> #("badge-pending", "Pending")
    types.InWork -> #("badge-progress", "In Progress")
    types.Finished -> #("badge-success", "Completed")
    types.Failed -> #("badge-danger", "Failed")
    types.Paused -> #("badge-paused", "Paused")
  }

  let can_fill = permissions.can_fill_record(record, model.user)
  let can_edit = permissions.can_edit_record(record, model.user)

  html.tr([], [
    html.td([], [html.text(record_id_str)]),
    html.td([], [html.text(type_label)]),
    html.td([], [
      html.span([attribute.class("badge " <> status_class)], [
        html.text(status_text),
      ]),
    ]),
    html.td([], [html.text(record.patient_id)]),
    html.td([], [
      case can_fill, can_edit {
        True, _ ->
          html.a(
            [
              attribute.href("/records/" <> record_id_str),
              attribute.class("btn btn-sm btn-primary"),
            ],
            [html.text("Fill")],
          )
        _, True ->
          html.a(
            [
              attribute.href("/records/" <> record_id_str),
              attribute.class("btn btn-sm btn-secondary"),
            ],
            [html.text("Edit")],
          )
        _, _ ->
          html.a(
            [
              attribute.href("/records/" <> record_id_str),
              attribute.class("btn btn-sm btn-outline"),
            ],
            [html.text("View")],
          )
      },
    ]),
  ])
}

import gleam/string
