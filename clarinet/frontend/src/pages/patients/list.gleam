// Patients list page — self-contained MVU module
import api/models
import clarinet_frontend/i18n.{type Key}
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
import modem
import router
import shared.{type OutMsg, type Shared}
import utils/table_sort.{type SortDirection, Asc, Desc}

// --- Model ---

pub type Model {
  Model(active_filters: Dict(String, String))
}

// --- Msg ---

pub type Msg {
  ColumnHeaderClicked(column: String)
}

// --- Init ---

const default_sort_col = "id"

pub fn init(
  filters: Dict(String, String),
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(Model(active_filters: filters), effect.none(), [shared.ReloadPatients])
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
      let new_filters = table_sort.write_sort(model.active_filters, new_col, new_dir)
      #(Model(active_filters: new_filters), sync_url_effect(new_filters), [])
    }
  }
}

fn sync_url_effect(filters: Dict(String, String)) -> Effect(Msg) {
  modem.replace(
    router.route_to_path(router.Patients(filters)),
    router.filters_to_query(filters),
    option.None,
  )
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  let t = shared.translate
  let #(sort_col, sort_dir) =
    table_sort.read_sort(model.active_filters, default_sort_col)
  let cmp = patient_comparator(sort_col, sort_dir)

  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text(t(i18n.PatientsTitle))]),
      html.a(
        [
          attribute.href(router.route_to_path(router.PatientNew)),
          attribute.class("btn btn-primary"),
        ],
        [html.text(t(i18n.BtnNewPatient))],
      ),
    ]),
    {
      let patients =
        dict.values(shared.cache.patients)
        |> list.sort(cmp)
      patients_table(patients, t, sort_col, sort_dir)
    },
  ])
}

fn patients_table(
  patients: List(models.Patient),
  translate: fn(Key) -> String,
  sort_col: String,
  sort_dir: SortDirection,
) -> Element(Msg) {
  case patients {
    [] ->
      html.p([attribute.class("text-muted")], [html.text(translate(i18n.PatientsNoFound))])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              table_sort.th_sortable(translate(i18n.ThId), "id", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThName), "name", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThAnonId), "anon_id", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThAnonName), "anon_name", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_sortable(translate(i18n.ThStudies), "studies_count", sort_col, sort_dir, ColumnHeaderClicked),
              table_sort.th_static(translate(i18n.ThActions)),
            ]),
          ]),
          html.tbody(
            [],
            list.map(patients, patient_row(_, translate)),
          ),
        ]),
      ])
  }
}

fn patient_comparator(
  col: String,
  dir: SortDirection,
) -> fn(models.Patient, models.Patient) -> order.Order {
  let base = case col {
    "id" -> fn(a: models.Patient, b: models.Patient) {
      string.compare(a.id, b.id)
    }
    "name" -> fn(a: models.Patient, b: models.Patient) {
      string.compare(option.unwrap(a.name, ""), option.unwrap(b.name, ""))
    }
    "anon_id" -> fn(a: models.Patient, b: models.Patient) {
      string.compare(
        option.unwrap(a.anon_id, ""),
        option.unwrap(b.anon_id, ""),
      )
    }
    "anon_name" -> fn(a: models.Patient, b: models.Patient) {
      string.compare(
        option.unwrap(a.anon_name, ""),
        option.unwrap(b.anon_name, ""),
      )
    }
    "studies_count" -> fn(a: models.Patient, b: models.Patient) {
      int.compare(
        list.length(option.unwrap(a.studies, [])),
        list.length(option.unwrap(b.studies, [])),
      )
    }
    _ -> fn(a: models.Patient, b: models.Patient) {
      string.compare(a.id, b.id)
    }
  }
  case dir {
    Asc -> base
    Desc -> fn(a, b) { order.negate(base(a, b)) }
  }
}

fn patient_row(patient: models.Patient, translate: fn(Key) -> String) -> Element(Msg) {
  let studies_count = case patient.studies {
    Some(studies) -> int.to_string(list.length(studies))
    None -> "0"
  }

  html.tr([], [
    html.td([], [html.text(patient.id)]),
    html.td([], [html.text(option.unwrap(patient.name, "-"))]),
    html.td([], [html.text(option.unwrap(patient.anon_id, "-"))]),
    html.td([], [html.text(option.unwrap(patient.anon_name, "-"))]),
    html.td([], [html.text(studies_count)]),
    html.td([], [
      html.a(
        [
          attribute.href(router.route_to_path(router.PatientDetail(patient.id))),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text(translate(i18n.BtnView))],
      ),
    ]),
  ])
}
