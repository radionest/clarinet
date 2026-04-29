// Studies list page — self-contained MVU module
import api/models
import clarinet_frontend/i18n.{type Key}
import gleam/dict.{type Dict}
import gleam/int
import gleam/list
import gleam/option
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

const default_sort_col = "study_uid"

pub fn init(
  filters: Dict(String, String),
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(Model(active_filters: filters), effect.none(), [shared.ReloadStudies])
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
        table_sort.write_sort(model.active_filters, new_col, new_dir, default_sort_col)
      #(Model(active_filters: new_filters), sync_url_effect(new_filters), [])
    }
  }
}

fn sync_url_effect(filters: Dict(String, String)) -> Effect(Msg) {
  url.replace_state(
    router.route_to_path(router.Studies(filters)),
    router.filters_to_query(filters),
  )
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let t = shared.translate
  let #(sort_col, sort_dir) =
    table_sort.read_sort(model.active_filters, default_sort_col)
  let cmp = study_comparator(sort_col, sort_dir)

  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text(t(i18n.StudiesTitle))]),
    ]),
    {
      let studies =
        dict.values(shared.cache.studies)
        |> list.sort(cmp)
      studies_table(studies, t, sort_col, sort_dir)
    },
  ])
}

fn studies_table(
  studies: List(models.Study),
  translate: fn(Key) -> String,
  sort_col: String,
  sort_dir: SortDirection,
) -> Element(Msg) {
  case studies {
    [] ->
      html.p([attribute.class("text-muted")], [html.text(translate(i18n.StudiesNoFound))])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              table_sort.th_sortable(translate(i18n.ThStudyUid), "study_uid", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThDate), "date", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThPatient), "patient_id", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThAnonUid), "anon_uid", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThSeries), "series_count", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_static(translate(i18n.ThActions)),
            ]),
          ]),
          html.tbody([], list.map(studies, study_row(_, translate))),
        ]),
      ])
  }
}

fn study_comparator(
  col: String,
  dir: SortDirection,
) -> fn(models.Study, models.Study) -> order.Order {
  let base = case col {
    "study_uid" -> fn(a: models.Study, b: models.Study) {
      string.compare(a.study_uid, b.study_uid)
    }
    "date" -> fn(a: models.Study, b: models.Study) {
      string.compare(a.date, b.date)
    }
    "patient_id" -> fn(a: models.Study, b: models.Study) {
      string.compare(a.patient_id, b.patient_id)
    }
    "anon_uid" -> fn(a: models.Study, b: models.Study) {
      string.compare(
        option.unwrap(a.anon_uid, ""),
        option.unwrap(b.anon_uid, ""),
      )
    }
    "series_count" -> fn(a: models.Study, b: models.Study) {
      int.compare(
        list.length(option.unwrap(a.series, [])),
        list.length(option.unwrap(b.series, [])),
      )
    }
    _ -> fn(a: models.Study, b: models.Study) {
      string.compare(a.study_uid, b.study_uid)
    }
  }
  table_sort.with_direction(base, dir)
}

fn study_row(study: models.Study, translate: fn(Key) -> String) -> Element(Msg) {
  let series_count = case study.series {
    option.Some(s) -> int.to_string(list.length(s))
    option.None -> "-"
  }

  html.tr([], [
    html.td([], [html.text(study.study_uid)]),
    html.td([], [html.text(study.date)]),
    html.td([], [html.text(study.patient_id)]),
    html.td([], [html.text(option.unwrap(study.anon_uid, "-"))]),
    html.td([], [html.text(series_count)]),
    html.td([], [
      html.a(
        [
          attribute.href(router.route_to_path(router.StudyDetail(study.study_uid))),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text(translate(i18n.BtnView))],
      ),
    ]),
  ])
}
