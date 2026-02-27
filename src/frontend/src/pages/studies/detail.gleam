// Study detail page (admin only)
import api/models.{type Patient, type Record, type Series, type Study}
import api/types
import gleam/dict
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}
import utils/viewer

pub fn view(model: Model, study_uid: String) -> Element(Msg) {
  case dict.get(model.studies, study_uid) {
    Ok(study) -> render_detail(model, study)
    Error(_) -> loading_view(study_uid)
  }
}

fn render_detail(model: Model, study: Study) -> Element(Msg) {
  let study_records =
    dict.values(model.records)
    |> list.filter(fn(r) { r.study_uid == Some(study.study_uid) })
    |> list.sort(fn(a, b) {
      int.compare(option.unwrap(a.id, 0), option.unwrap(b.id, 0))
    })

  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Study: " <> study.study_uid)]),
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(store.Navigate(router.Studies)),
        ],
        [html.text("Back to Studies")],
      ),
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
    html.td([], [html.text(status_text(record.status))]),
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

fn loading_view(study_uid: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading study " <> study_uid <> "...")]),
  ])
}
