// Study detail page — self-contained MVU module
import api/models.{type Patient, type Record, type Series, type Study}
import api/studies
import api/types.{type ApiError, AuthError}
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/javascript/promise
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/status
import utils/viewer

// --- Model ---

pub type Model {
  Model(study_uid: String)
}

// --- Msg ---

pub type Msg {
  StudyLoaded(Result(Study, ApiError))
  Delete
  DeleteResult(Result(Nil, ApiError))
  NavigateBack
  RequestDelete
}

// --- Init ---

pub fn init(study_uid: String, _shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model = Model(study_uid: study_uid)
  let eff = {
    use dispatch <- effect.from
    studies.get_study(study_uid)
    |> promise.tap(fn(result) { dispatch(StudyLoaded(result)) })
    Nil
  }
  #(model, eff, [shared.ReloadRecords])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    StudyLoaded(Ok(study)) ->
      #(model, effect.none(), [shared.CacheStudy(study)])

    StudyLoaded(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to load study"))

    Delete -> {
      let eff = {
        use dispatch <- effect.from
        studies.delete_study(model.study_uid)
        |> promise.tap(fn(result) { dispatch(DeleteResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    DeleteResult(Ok(_)) ->
      #(model, effect.none(), [
        shared.SetLoading(False),
        shared.ReloadStudies,
        shared.ShowSuccess("Study deleted successfully"),
        shared.Navigate(router.Studies),
      ])

    DeleteResult(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to delete study"))

    NavigateBack ->
      #(model, effect.none(), [shared.Navigate(router.Studies)])

    RequestDelete ->
      #(model, effect.none(), [
        shared.OpenDeleteConfirm("study", model.study_uid),
      ])
  }
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  case dict.get(shared.studies, model.study_uid) {
    Ok(study) -> render_detail(shared, study)
    Error(_) -> loading_view(model.study_uid)
  }
}

fn render_detail(shared: Shared, study: Study) -> Element(Msg) {
  let study_records =
    dict.values(shared.records)
    |> list.filter(fn(r) { r.study_uid == Some(study.study_uid) })
    |> list.sort(fn(a, b) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    })

  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Study: " <> study.study_uid)]),
      html.div([], [
        html.button(
          [
            attribute.class("btn btn-secondary"),
            event.on_click(NavigateBack),
          ],
          [html.text("Back to Studies")],
        ),
        html.button(
          [
            attribute.class("btn btn-danger"),
            event.on_click(RequestDelete),
          ],
          [html.text("Delete Study")],
        ),
      ]),
    ]),
    study_info_card(study),
    patient_section(study.patient, study.patient_id),
    series_section(study.series),
    records_section(study_records),
  ])
}

fn study_info_card(study: Study) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Study Information")]),
    html.dl([attribute.class("record-metadata")], [
      html.dt([], [html.text("Study UID:")]),
      html.dd([], [html.text(study.study_uid)]),
      html.dt([], [html.text("Date:")]),
      html.dd([], [html.text(study.date)]),
      html.dt([], [html.text("Anonymous UID:")]),
      html.dd([], [html.text(option.unwrap(study.anon_uid, "-"))]),
      html.dt([], [html.text("Patient ID:")]),
      html.dd([], [html.text(study.patient_id)]),
    ]),
    html.div([attribute.class("card-actions")], [
      viewer.viewer_button(Some(study.study_uid), None, "btn btn-primary"),
    ]),
  ])
}

fn patient_section(patient: Option(Patient), patient_id: String) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Patient")]),
    case patient {
      None ->
        html.p([], [
          html.a(
            [
              attribute.href(
                router.route_to_path(router.PatientDetail(patient_id)),
              ),
              attribute.class("link"),
            ],
            [html.text(patient_id)],
          ),
        ])
      Some(p) ->
        html.div([], [
          html.dl([attribute.class("record-metadata")], [
            html.dt([], [html.text("ID:")]),
            html.dd([], [
              html.a(
                [
                  attribute.href(
                    router.route_to_path(router.PatientDetail(p.id)),
                  ),
                  attribute.class("link"),
                ],
                [html.text(p.id)],
              ),
            ]),
            html.dt([], [html.text("Name:")]),
            html.dd([], [html.text(option.unwrap(p.name, "-"))]),
            html.dt([], [html.text("Anon ID:")]),
            html.dd([], [html.text(option.unwrap(p.anon_id, "-"))]),
          ]),
        ])
    },
  ])
}

fn series_section(series: Option(List(Series))) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Series")]),
    case series {
      None | Some([]) ->
        html.p([attribute.class("text-muted")], [
          html.text("No series found for this study."),
        ])
      Some(series_list) ->
        html.div([attribute.class("table-responsive")], [
          html.table([attribute.class("table")], [
            html.thead([], [
              html.tr([], [
                html.th([], [html.text("Series UID")]),
                html.th([], [html.text("Description")]),
                html.th([], [html.text("Number")]),
                html.th([], [html.text("Anon UID")]),
                html.th([], [html.text("Actions")]),
              ]),
            ]),
            html.tbody([], list.map(series_list, series_row)),
          ]),
        ])
    },
  ])
}

fn series_row(s: Series) -> Element(Msg) {
  html.tr([], [
    html.td([], [html.text(s.series_uid)]),
    html.td([], [html.text(option.unwrap(s.series_description, "-"))]),
    html.td([], [html.text(int.to_string(s.series_number))]),
    html.td([], [html.text(option.unwrap(s.anon_uid, "-"))]),
    html.td([], [
      element.fragment([
        html.a(
          [
            attribute.href(
              router.route_to_path(router.SeriesDetail(s.series_uid)),
            ),
            attribute.class("btn btn-sm btn-outline"),
          ],
          [html.text("View")],
        ),
        viewer.viewer_button(
          Some(s.study_uid),
          Some(s.series_uid),
          "btn btn-sm btn-outline",
        ),
      ]),
    ]),
  ])
}

fn records_section(records: List(Record)) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Records")]),
    case records {
      [] ->
        html.p([attribute.class("text-muted")], [
          html.text("No records found for this study."),
        ])
      _ ->
        html.div([attribute.class("table-responsive")], [
          html.table([attribute.class("table")], [
            html.thead([], [
              html.tr([], [
                html.th([], [html.text("ID")]),
                html.th([], [html.text("Type")]),
                html.th([], [html.text("Status")]),
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

  let type_label = case record.record_type {
    Some(rt) -> option.unwrap(rt.label, rt.name)
    None -> record.record_type_name
  }

  html.tr([], [
    html.td([], [html.text(record_id_str)]),
    html.td([], [html.text(type_label)]),
    html.td([], [html.text(status.display_text(record.status))]),
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

fn loading_view(study_uid: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading study " <> study_uid <> "...")]),
  ])
}
