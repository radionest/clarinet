// Series detail page (admin only)
import api/models.{type Record, type Series}
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

pub fn view(model: Model, series_uid: String) -> Element(Msg) {
  case dict.get(model.series, series_uid) {
    Ok(s) -> render_detail(s)
    Error(_) -> loading_view(series_uid)
  }
}

fn render_detail(s: Series) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Series: " <> s.series_uid)]),
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(store.Navigate(router.StudyDetail(s.study_uid))),
        ],
        [html.text("Back to Study")],
      ),
    ]),
    series_info_card(s),
    parent_study_section(s),
    records_section(s.records),
  ])
}

fn series_info_card(s: Series) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Series Information")]),
    html.dl([attribute.class("record-metadata")], [
      html.dt([], [html.text("Series UID:")]),
      html.dd([], [html.text(s.series_uid)]),
      html.dt([], [html.text("Description:")]),
      html.dd([], [html.text(option.unwrap(s.series_description, "-"))]),
      html.dt([], [html.text("Number:")]),
      html.dd([], [html.text(int.to_string(s.series_number))]),
      html.dt([], [html.text("Anonymous UID:")]),
      html.dd([], [html.text(option.unwrap(s.anon_uid, "-"))]),
      html.dt([], [html.text("Working Folder:")]),
      html.dd([], [html.text(option.unwrap(s.working_folder, "-"))]),
      html.dt([], [html.text("Study UID:")]),
      html.dd([], [
        html.a(
          [
            attribute.href(router.route_to_path(router.StudyDetail(s.study_uid))),
            attribute.class("link"),
          ],
          [html.text(s.study_uid)],
        ),
      ]),
    ]),
  ])
}

fn parent_study_section(s: Series) -> Element(Msg) {
  case s.study {
    None -> element.none()
    Some(study) ->
      html.div([attribute.class("card")], [
        html.h3([], [html.text("Parent Study")]),
        html.dl([attribute.class("record-metadata")], [
          html.dt([], [html.text("Study UID:")]),
          html.dd([], [
            html.a(
              [
                attribute.href(router.route_to_path(router.StudyDetail(study.study_uid))),
                attribute.class("link"),
              ],
              [html.text(study.study_uid)],
            ),
          ]),
          html.dt([], [html.text("Date:")]),
          html.dd([], [html.text(study.date)]),
          html.dt([], [html.text("Patient ID:")]),
          html.dd([], [
            html.a(
              [
                attribute.href(router.route_to_path(router.PatientDetail(study.patient_id))),
                attribute.class("link"),
              ],
              [html.text(study.patient_id)],
            ),
          ]),
        ]),
      ])
  }
}

fn records_section(records: option.Option(List(Record))) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h3([], [html.text("Records")]),
    case records {
      None | Some([]) ->
        html.p([attribute.class("text-muted")], [
          html.text("No records found for this series."),
        ])
      Some(record_list) ->
        html.div([attribute.class("table-responsive")], [
          html.table([attribute.class("table")], [
            html.thead([], [
              html.tr([], [
                html.th([], [html.text("ID")]),
                html.th([], [html.text("Type")]),
                html.th([], [html.text("Status")]),
                html.th([], [html.text("Patient")]),
                html.th([], [html.text("Actions")]),
              ]),
            ]),
            html.tbody([], list.map(record_list, record_row)),
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
    html.td([], [html.text(record.record_type_name)]),
    html.td([], [html.text(status_text(record.status))]),
    html.td([], [html.text(record.patient_id)]),
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

fn loading_view(series_uid: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading series " <> series_uid <> "...")]),
  ])
}
