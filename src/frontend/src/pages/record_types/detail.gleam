// Record type detail page (admin only)
import api/models.{type Record, type RecordTypeStats}
import api/types
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}

pub fn view(model: Model, name: String) -> Element(Msg) {
  case find_stats(model, name) {
    Some(stat) -> render_detail(model, stat)
    None -> loading_view(name)
  }
}

fn find_stats(
  model: Model,
  name: String,
) -> option.Option(RecordTypeStats) {
  case model.record_type_stats {
    Some(stats) -> list.find(stats, fn(s) { s.name == name }) |> option.from_result
    None -> None
  }
}

fn render_detail(model: Model, stat: RecordTypeStats) -> Element(Msg) {
  let type_records =
    dict.values(model.records)
    |> list.filter(fn(r) { r.record_type_name == stat.name })
    |> list.sort(fn(a, b) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    })

  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [
        html.text("Record Type: " <> option.unwrap(stat.label, stat.name)),
      ]),
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(store.Navigate(router.AdminRecordTypes)),
        ],
        [html.text("Back to Record Types")],
      ),
      html.button(
        [
          attribute.class("btn btn-primary"),
          event.on_click(store.Navigate(router.AdminRecordTypeEdit(stat.name))),
        ],
        [html.text("Edit")],
      ),
    ]),
    info_card(stat),
    stats_cards(stat),
    records_section(type_records),
  ])
}

fn info_card(stat: RecordTypeStats) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Record Type Information")]),
    html.dl([attribute.class("record-metadata")], [
      html.dt([], [html.text("Name:")]),
      html.dd([], [html.text(stat.name)]),
      html.dt([], [html.text("Label:")]),
      html.dd([], [html.text(option.unwrap(stat.label, "-"))]),
      html.dt([], [html.text("Description:")]),
      html.dd([], [html.text(option.unwrap(stat.description, "-"))]),
      html.dt([], [html.text("Level:")]),
      html.dd([], [html.text(stat.level)]),
      html.dt([], [html.text("Role:")]),
      html.dd([], [html.text(option.unwrap(stat.role_name, "-"))]),
      html.dt([], [html.text("Min Users:")]),
      html.dd([], [
        html.text(case stat.min_users {
          Some(n) -> int.to_string(n)
          None -> "-"
        }),
      ]),
      html.dt([], [html.text("Max Users:")]),
      html.dd([], [
        html.text(case stat.max_users {
          Some(n) -> int.to_string(n)
          None -> "-"
        }),
      ]),
    ]),
  ])
}

fn stats_cards(stat: RecordTypeStats) -> Element(Msg) {
  let counts = stat.records_by_status
  html.div([attribute.class("stats-grid")], [
    stat_card("Total Records", int.to_string(stat.total_records)),
    stat_card("Unique Users", int.to_string(stat.unique_users)),
    stat_card("Pending", int.to_string(counts.pending)),
    stat_card("In Work", int.to_string(counts.inwork)),
    stat_card("Finished", int.to_string(counts.finished)),
    stat_card("Failed", int.to_string(counts.failed)),
    stat_card("Paused", int.to_string(counts.pause)),
  ])
}

fn stat_card(label: String, value: String) -> Element(Msg) {
  html.div([attribute.class("card stat-card")], [
    html.div([attribute.class("stat-value")], [html.text(value)]),
    html.div([attribute.class("stat-label")], [html.text(label)]),
  ])
}

fn records_section(records: List(Record)) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Records")]),
    case records {
      [] ->
        html.p([attribute.class("text-muted")], [
          html.text("No records found for this type."),
        ])
      _ ->
        html.div([attribute.class("table-responsive")], [
          html.table([attribute.class("table")], [
            html.thead([], [
              html.tr([], [
                html.th([], [html.text("ID")]),
                html.th([], [html.text("Patient")]),
                html.th([], [html.text("Status")]),
                html.th([], [html.text("User")]),
                html.th([], [html.text("Actions")]),
              ]),
            ]),
            html.tbody([], list.map(records, record_row)),
          ]),
        ])
    },
  ])
}

fn record_row(record: Record) -> Element(Msg) {
  let record_id = option.unwrap(record.id, 0)
  let record_id_str = int.to_string(record_id)

  html.tr([], [
    html.td([], [html.text(record_id_str)]),
    html.td([], [html.text(record.patient_id)]),
    html.td([], [html.text(status_text(record.status))]),
    html.td([], [html.text(option.unwrap(record.user_id, "-"))]),
    html.td([], [
      html.a(
        [
          attribute.href("/records/" <> record_id_str),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text("View")],
      ),
    ]),
  ])
}

fn status_text(status: types.RecordStatus) -> String {
  case status {
    types.Pending -> "Pending"
    types.InWork -> "In Progress"
    types.Finished -> "Completed"
    types.Failed -> "Failed"
    types.Paused -> "Paused"
  }
}

fn loading_view(name: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading record type " <> name <> "...")]),
  ])
}
