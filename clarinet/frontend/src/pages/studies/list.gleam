// Studies list page — self-contained MVU module
import api/models
import gleam/dict
import gleam/int
import gleam/list
import gleam/option
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
  #(Model, effect.none(), [shared.ReloadStudies])
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
      html.h1([], [html.text("Studies")]),
    ]),
    {
      let studies =
        dict.values(shared.studies)
        |> list.sort(fn(a, b) { string.compare(a.study_uid, b.study_uid) })
      studies_table(studies)
    },
  ])
}

fn studies_table(studies: List(models.Study)) -> Element(Msg) {
  case studies {
    [] ->
      html.p([attribute.class("text-muted")], [html.text("No studies found.")])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text("Study UID")]),
              html.th([], [html.text("Date")]),
              html.th([], [html.text("Patient ID")]),
              html.th([], [html.text("Anon UID")]),
              html.th([], [html.text("Series")]),
              html.th([], [html.text("Actions")]),
            ]),
          ]),
          html.tbody([], list.map(studies, study_row)),
        ]),
      ])
  }
}

fn study_row(study: models.Study) -> Element(Msg) {
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
        [html.text("View")],
      ),
    ]),
  ])
}
