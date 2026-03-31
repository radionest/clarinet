// Patients list page — self-contained MVU module
import api/models
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{None, Some}
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import router
import shared.{type OutMsg, type Shared}

// --- Model ---

pub type Model {
  Model
}

// --- Msg ---

pub type Msg {
  NoOp
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg)) {
  #(Model, effect.none())
}

// --- Update ---

pub fn update(
  model: Model,
  _msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(model, effect.none(), [])
}

// --- View ---

pub fn view(_model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Patients")]),
      html.a(
        [
          attribute.href(router.route_to_path(router.PatientNew)),
          attribute.class("btn btn-primary"),
        ],
        [html.text("New Patient")],
      ),
    ]),
    {
      let patients =
        dict.values(shared.patients)
        |> list.sort(fn(a, b) { string.compare(a.id, b.id) })
      patients_table(patients)
    },
  ])
}

fn patients_table(patients: List(models.Patient)) -> Element(Msg) {
  case patients {
    [] ->
      html.p([attribute.class("text-muted")], [html.text("No patients found.")])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text("ID")]),
              html.th([], [html.text("Name")]),
              html.th([], [html.text("Anon ID")]),
              html.th([], [html.text("Anon Name")]),
              html.th([], [html.text("Studies")]),
              html.th([], [html.text("Actions")]),
            ]),
          ]),
          html.tbody(
            [],
            list.map(patients, patient_row),
          ),
        ]),
      ])
  }
}

fn patient_row(patient: models.Patient) -> Element(Msg) {
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
        [html.text("View")],
      ),
    ]),
  ])
}
