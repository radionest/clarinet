// Record types list page (admin only)
import api/models
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import router
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Record Types")]),
    ]),
    case model.loading, model.record_type_stats {
      True, _ ->
        html.div([attribute.class("loading")], [
          html.p([], [html.text("Loading record types...")]),
        ])
      False, None ->
        html.p([attribute.class("text-muted")], [
          html.text("No record type data available."),
        ])
      False, Some(stats) -> record_types_table(stats)
    },
  ])
}

fn record_types_table(
  stats: List(models.RecordTypeStats),
) -> Element(Msg) {
  case stats {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text("No record types found."),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text("Name")]),
              html.th([], [html.text("Label")]),
              html.th([], [html.text("Level")]),
              html.th([], [html.text("Role")]),
              html.th([], [html.text("Min/Max Users")]),
              html.th([], [html.text("Total Records")]),
              html.th([], [html.text("Pending")]),
              html.th([], [html.text("In Work")]),
              html.th([], [html.text("Finished")]),
              html.th([], [html.text("Failed")]),
              html.th([], [html.text("Unique Users")]),
              html.th([], [html.text("Actions")]),
            ]),
          ]),
          html.tbody([], list.map(stats, record_type_row)),
        ]),
      ])
  }
}

fn record_type_row(stat: models.RecordTypeStats) -> Element(Msg) {
  let min_max = case stat.min_users, stat.max_users {
    Some(min), Some(max) -> int.to_string(min) <> "/" <> int.to_string(max)
    Some(min), None -> int.to_string(min) <> "/-"
    None, Some(max) -> "-/" <> int.to_string(max)
    None, None -> "-"
  }

  html.tr([], [
    html.td([], [
      html.a(
        [
          attribute.href(
            router.route_to_path(router.AdminRecordTypeDetail(stat.name)),
          ),
          attribute.class("link"),
        ],
        [html.text(stat.name)],
      ),
    ]),
    html.td([], [html.text(option.unwrap(stat.label, "-"))]),
    html.td([], [html.text(stat.level)]),
    html.td([], [html.text(option.unwrap(stat.role_name, "-"))]),
    html.td([], [html.text(min_max)]),
    html.td([], [html.text(int.to_string(stat.total_records))]),
    html.td([], [html.text(int.to_string(stat.records_by_status.pending))]),
    html.td([], [html.text(int.to_string(stat.records_by_status.inwork))]),
    html.td([], [html.text(int.to_string(stat.records_by_status.finished))]),
    html.td([], [html.text(int.to_string(stat.records_by_status.failed))]),
    html.td([], [html.text(int.to_string(stat.unique_users))]),
    html.td([], [
      html.a(
        [
          attribute.href(
            router.route_to_path(router.AdminRecordTypeDetail(stat.name)),
          ),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text("View")],
      ),
    ]),
  ])
}
