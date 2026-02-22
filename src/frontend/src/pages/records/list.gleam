// Records list page
import api/models.{type Record}
import api/types
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
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
      False -> records_table(model)
    },
  ])
}

fn records_table(model: Model) -> Element(Msg) {
  let records =
    dict.values(model.records)
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
    Some(rt) ->
      case rt.label {
        Some(l) -> l
        None -> rt.name
      }
    None -> record.record_type_name
  }

  let #(status_class, status_text) = case record.status {
    types.Pending -> #("badge-pending", "Pending")
    types.InWork -> #("badge-progress", "In Progress")
    types.Finished -> #("badge-success", "Completed")
    types.Failed -> #("badge-danger", "Failed")
    types.Paused -> #("badge-paused", "Paused")
  }

  let can_fill = case record.status {
    types.Pending | types.InWork -> {
      case model.user {
        Some(u) ->
          case record.user_id {
            Some(assigned_id) -> assigned_id == u.id || u.is_superuser
            None -> u.is_superuser
          }
        None -> False
      }
    }
    _ -> False
  }

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
      case can_fill {
        True ->
          html.a(
            [
              attribute.href("/records/" <> record_id_str),
              attribute.class("btn btn-sm btn-primary"),
            ],
            [html.text("Fill")],
          )
        False ->
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
