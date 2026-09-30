// Record types list page (admin only) — self-contained MVU module
import api/models.{type RecordTypeStats}
import gleam/dict.{type Dict}
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import gleam/order
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import router
import shared.{type OutMsg, type Shared}
import utils/table_sort.{type SortDirection}
import utils/url

// --- Model ---

pub type Model {
  Model(active_filters: Dict(String, String))
}

// --- Msg ---

pub type Msg {
  ColumnHeaderClicked(column: String)
}

// --- Init ---

const default_sort_col = "name"

pub fn init(
  filters: Dict(String, String),
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(Model(active_filters: filters), effect.none(), [
    shared.ReloadRecordTypeStats,
  ])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
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
      #(
        Model(active_filters: new_filters),
        url.replace_route(router.AdminRecordTypes(new_filters)),
        [],
      )
    }
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let #(sort_col, sort_dir) =
    table_sort.read_sort(model.active_filters, default_sort_col)

  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Record Types")]),
    ]),
    case shared.cache.record_type_stats {
      None ->
        html.p([attribute.class("text-muted")], [
          html.text("No record type data available."),
        ])
      Some(stats) ->
        stats
        |> list.sort(record_type_comparator(sort_col, sort_dir))
        |> record_types_table(sort_col, sort_dir)
    },
  ])
}

fn record_types_table(
  stats: List(RecordTypeStats),
  sort_col: String,
  sort_dir: SortDirection,
) -> Element(Msg) {
  let th = fn(label, key) {
    table_sort.th_sortable(label, key, sort_col, sort_dir, ColumnHeaderClicked)
  }
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
              th("Name", "name"),
              th("Label", "label"),
              th("Level", "level"),
              th("Role", "role"),
              table_sort.th_static("Min/Max Users"),
              th("Total Records", "total_records"),
              th("Pending", "pending"),
              th("In Work", "inwork"),
              th("Finished", "finished"),
              th("Failed", "failed"),
              th("Unique Users", "unique_users"),
              table_sort.th_static("Actions"),
            ]),
          ]),
          html.tbody([], list.map(stats, record_type_row)),
        ]),
      ])
  }
}

fn record_type_comparator(
  col: String,
  dir: SortDirection,
) -> fn(RecordTypeStats, RecordTypeStats) -> order.Order {
  let by_string = fn(get: fn(RecordTypeStats) -> String) {
    fn(a, b) { string.compare(get(a), get(b)) }
  }
  let by_int = fn(get: fn(RecordTypeStats) -> Int) {
    fn(a, b) { int.compare(get(a), get(b)) }
  }
  let base = case col {
    "label" -> by_string(fn(s) { option.unwrap(s.label, "") })
    "level" -> by_string(fn(s) { s.level })
    "role" -> by_string(fn(s) { option.unwrap(s.role_name, "") })
    "total_records" -> by_int(fn(s) { s.total_records })
    "pending" -> by_int(fn(s) { s.records_by_status.pending })
    "inwork" -> by_int(fn(s) { s.records_by_status.inwork })
    "finished" -> by_int(fn(s) { s.records_by_status.finished })
    "failed" -> by_int(fn(s) { s.records_by_status.failed })
    "unique_users" -> by_int(fn(s) { s.unique_users })
    _ -> by_string(fn(s) { s.name })
  }
  table_sort.with_direction(base, dir)
}

fn record_type_row(stat: RecordTypeStats) -> Element(Msg) {
  let min_max = case stat.min_records, stat.max_records {
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
