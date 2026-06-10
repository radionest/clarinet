// Reusable records-list widget — filter bar + sortable table shared by the
// Records, Patient detail and Admin pages. Stateless and polymorphic in the
// page's `msg`; the page owns its Model/Msg/update and supplies the filter
// callbacks plus the three cells that genuinely diverge between pages
// (status, assigned-user, actions) through `Config`.
import api/models.{type Record}
import api/types
import cache/bucket.{type BucketStatus}
import clarinet_frontend/i18n.{type Key}
import components/forms/base
import gleam/dict.{type Dict}
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type Shared}
import utils/record_filters
import utils/table_sort.{type SortDirection}

/// Baseline sort column. Single source of truth shared with the consuming
/// pages' `ColumnHeaderClicked` arms, so the arrow the header draws agrees
/// with the order `records_query.parse_sort_from_filters` asks the server for.
pub const default_sort_col = "id"

/// Per-page configuration. Boolean flags toggle optional filters/columns;
/// the `*_cell` callbacks render the cells that differ between pages and
/// close over page-local state (admin's inline-edit buffers, permissions).
pub type Config(msg) {
  Config(
    // Filter bar
    show_type_filter: Bool,
    show_patient_filter: Bool,
    show_user_filter: Bool,
    // Optional columns
    show_patient_columns: Bool,
    show_study_series: Bool,
    show_modality: Bool,
    // Shown when the (loaded) list is empty
    empty_message: String,
    // Filter-bar callbacks
    on_add_filter: fn(String, String) -> msg,
    on_remove_filter: fn(String) -> msg,
    on_clear_filters: msg,
    on_column_click: fn(String) -> msg,
    // Diverging cells. `user_cell` is None when the page has no assigned-user
    // column at all (patient detail); Some renders both the column and cell.
    status_cell: fn(Record) -> Element(msg),
    user_cell: Option(fn(Record) -> Element(msg)),
    actions_cell: fn(Record) -> Element(msg),
  )
}

/// Render the filter bar plus the records table. The caller resolves the
/// bucket key (scope differs per page) and passes the items + status; the
/// widget owns the loading/empty/error presentation. Returned as a fragment
/// so the page controls its own wrapper (container / card / section).
pub fn view(
  records: List(Record),
  status: BucketStatus,
  active_filters: Dict(String, String),
  shared: Shared,
  config: Config(msg),
) -> Element(msg) {
  let body = case status {
    bucket.Cold | bucket.Loading ->
      html.div([attribute.class("loading-indicator")], [
        html.text(shared.translate(i18n.LblLoading)),
      ])
    bucket.Failed(err) ->
      html.p([attribute.class("text-error")], [html.text(err)])
    _ ->
      case records {
        [] ->
          html.p([attribute.class("text-muted")], [
            html.text(config.empty_message),
          ])
        _ -> records_table(records, active_filters, shared, config)
      }
  }
  element.fragment([filter_bar(active_filters, shared, config), body])
}

/// Build a drill-in link to the record detail page. Shared by every page's
/// `actions_cell` so the anchor markup lives in one place.
pub fn detail_link(
  record: Record,
  class: String,
  label: Key,
  translate: fn(Key) -> String,
) -> Element(msg) {
  let id = int.to_string(option.unwrap(record.id, 0))
  html.a(
    [
      attribute.href(router.route_to_path(router.RecordDetail(id))),
      attribute.class(class),
    ],
    [html.text(translate(label))],
  )
}

// --- Filter bar ---

fn filter_bar(
  active_filters: Dict(String, String),
  shared: Shared,
  config: Config(msg),
) -> Element(msg) {
  let #(patient_values, type_values, user_values) = case
    shared.cache.filter_options
  {
    Some(opts) -> #(opts.patients, opts.record_types, opts.users)
    None -> #([], [], [])
  }

  let status_select =
    filter_select(
      active_filters,
      "status",
      "filter-status",
      record_filters.status_options(shared.translate),
      config,
    )
  let type_select = case config.show_type_filter {
    True -> [
      filter_select(
        active_filters,
        "record_type",
        "filter-record-type",
        record_filters.type_options(type_values, shared.translate),
        config,
      ),
    ]
    False -> []
  }
  let patient_select = case config.show_patient_filter {
    True -> [
      filter_select(
        active_filters,
        "patient",
        "filter-patient",
        record_filters.patient_options(patient_values, shared.translate),
        config,
      ),
    ]
    False -> []
  }
  let user_select = case config.show_user_filter {
    True -> [
      filter_select(
        active_filters,
        "user",
        "filter-user",
        record_filters.user_options(
          user_values,
          shared.cache.users,
          shared.translate,
        ),
        config,
      ),
    ]
    False -> []
  }
  let clear_button = case record_filters.has_user_filters(active_filters) {
    True -> [
      html.button(
        [
          attribute.type_("button"),
          attribute.class("btn btn-sm btn-outline"),
          event.on_click(config.on_clear_filters),
        ],
        [html.text(shared.translate(i18n.BtnClearFilters))],
      ),
    ]
    False -> []
  }

  html.div(
    [attribute.class("filter-bar")],
    list.flatten([
      [status_select],
      type_select,
      patient_select,
      user_select,
      clear_button,
    ]),
  )
}

fn filter_select(
  active_filters: Dict(String, String),
  key: String,
  name: String,
  options: List(#(String, String)),
  config: Config(msg),
) -> Element(msg) {
  let value =
    dict.get(active_filters, key)
    |> option.from_result()
    |> option.unwrap("")
  base.select(name: name, value: value, options: options, on_change: fn(val) {
    case val {
      "" -> config.on_remove_filter(key)
      _ -> config.on_add_filter(key, val)
    }
  })
}

// --- Table ---

fn records_table(
  records: List(Record),
  active_filters: Dict(String, String),
  shared: Shared,
  config: Config(msg),
) -> Element(msg) {
  // Filtering and sorting are server-side via the bucket key. The
  // (sort_col, sort_dir) pair is read only to draw the header arrows.
  let #(sort_col, sort_dir) =
    table_sort.read_sort(active_filters, default_sort_col)
  html.div([attribute.class("table-responsive")], [
    html.table([attribute.class("table")], [
      html.thead([], [header_row(sort_col, sort_dir, shared, config)]),
      html.tbody(
        [],
        list.map(records, fn(record) { record_row(record, config) }),
      ),
    ]),
  ])
}

fn header_row(
  sort_col: String,
  sort_dir: SortDirection,
  shared: Shared,
  config: Config(msg),
) -> Element(msg) {
  let t = shared.translate
  let sortable = fn(label_key: Key, key: String) {
    table_sort.th_sortable(
      t(label_key),
      key,
      sort_col,
      sort_dir,
      config.on_column_click,
    )
  }
  html.tr(
    [],
    list.flatten([
      [
        sortable(i18n.ThId, "id"),
        sortable(i18n.ThRecordType, "record_type"),
        sortable(i18n.ThStatus, "status"),
      ],
      case config.show_patient_columns {
        True -> [
          table_sort.th_static(t(i18n.ThPatientName)),
          sortable(i18n.ThPatientId, "patient"),
          table_sort.th_static(t(i18n.ThAnonId)),
        ]
        False -> []
      },
      case config.show_study_series {
        True -> [table_sort.th_static(t(i18n.ThStudySeries))]
        False -> []
      },
      case config.show_modality {
        True -> [sortable(i18n.ThModality, "modality")]
        False -> []
      },
      case config.user_cell {
        Some(_) -> [sortable(i18n.ThAssignedUser, "user")]
        None -> []
      },
      [table_sort.th_static(t(i18n.ThActions))],
    ]),
  )
}

fn record_row(record: Record, config: Config(msg)) -> Element(msg) {
  let record_id_str = int.to_string(option.unwrap(record.id, 0))
  let type_label = case record.record_type {
    Some(rt) -> option.unwrap(rt.label, rt.name)
    None -> record.record_type_name
  }
  html.tr(
    [],
    list.flatten([
      [
        html.td([], [html.text(record_id_str)]),
        html.td([], [html.text(type_label)]),
        html.td([], [config.status_cell(record)]),
      ],
      case config.show_patient_columns {
        True -> [
          html.td([], [html.text(patient_name(record))]),
          html.td([], [html.text(record.patient_id)]),
          html.td([], [html.text(patient_anon_id(record))]),
        ]
        False -> []
      },
      case config.show_study_series {
        True -> [html.td([], [html.text(study_series_text(record))])]
        False -> []
      },
      case config.show_modality {
        True -> [html.td([], [html.text(record_modality_text(record))])]
        False -> []
      },
      case config.user_cell {
        Some(render) -> [html.td([], [render(record)])]
        None -> []
      },
      [html.td([], [config.actions_cell(record)])],
    ]),
  )
}

// --- Cell helpers ---

fn patient_name(record: Record) -> String {
  case record.patient {
    Some(p) -> option.unwrap(p.name, "—")
    None -> "—"
  }
}

fn patient_anon_id(record: Record) -> String {
  case record.display_anon_id {
    Some(id) -> id
    None ->
      case record.patient {
        Some(p) -> option.unwrap(p.anon_id, "—")
        None -> "—"
      }
  }
}

/// Resolve the modality column value: prefer series.modality, fall back to
/// study.modalities_in_study, then to a single dash. The same string is used
/// for display and as the sort key, so rows without a modality cluster
/// together consistently in both directions.
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

/// Study/Series column text. Patient-level records have no study by
/// definition, so they always render "-" regardless of any joined study.
fn study_series_text(record: Record) -> String {
  case record.record_type {
    Some(rt) ->
      case rt.level {
        types.Patient -> "-"
        _ -> format_study_series_summary(record)
      }
    None -> format_study_series_summary(record)
  }
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
