// Patients list page — self-contained MVU module
import api/models
import clarinet_frontend/i18n.{type Key}
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

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(Model, effect.none(), [shared.ReloadPatients])
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
  let t = shared.translate
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
        |> list.sort(fn(a, b) { string.compare(a.id, b.id) })
      patients_table(patients, t)
    },
  ])
}

fn patients_table(patients: List(models.Patient), translate: fn(Key) -> String) -> Element(Msg) {
  case patients {
    [] ->
      html.p([attribute.class("text-muted")], [html.text(translate(i18n.PatientsNoFound))])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text(translate(i18n.ThId))]),
              html.th([], [html.text(translate(i18n.ThName))]),
              html.th([], [html.text(translate(i18n.ThAnonId))]),
              html.th([], [html.text(translate(i18n.ThAnonName))]),
              html.th([], [html.text(translate(i18n.ThStudies))]),
              html.th([], [html.text(translate(i18n.ThActions))]),
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
