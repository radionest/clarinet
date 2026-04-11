// Series detail page — self-contained MVU module
import api/models.{type Record, type Series}
import api/series
import api/types.{type ApiError, AuthError}
import clarinet_frontend/i18n.{type Key}
import components/status_badge
import gleam/dict
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}
import utils/viewer

// --- Model ---

pub type Model {
  Model(series_uid: String, load_status: LoadStatus)
}

// --- Msg ---

pub type Msg {
  SeriesLoaded(Result(Series, ApiError))
  NavigateBack(study_uid: String)
  RetryLoad
}

// --- Init ---

pub fn init(
  series_uid: String,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model = Model(series_uid: series_uid, load_status: load_status.Loading)
  #(model, load_series_effect(series_uid), [])
}

fn load_series_effect(series_uid: String) -> Effect(Msg) {
  use dispatch <- effect.from
  series.get_series(series_uid)
  |> promise.tap(fn(result) { dispatch(SeriesLoaded(result)) })
  Nil
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    SeriesLoaded(Ok(s)) -> #(
      Model(..model, load_status: load_status.Loaded),
      effect.none(),
      [shared.CacheSeries(s)],
    )

    SeriesLoaded(Error(err)) -> #(
      Model(..model, load_status: load_status.Failed("Failed to load series")),
      effect.none(),
      handle_error(err, "Failed to load series"),
    )

    RetryLoad -> #(
      Model(..model, load_status: load_status.Loading),
      load_series_effect(model.series_uid),
      [],
    )

    NavigateBack(study_uid) -> #(model, effect.none(), [
      shared.Navigate(router.StudyDetail(study_uid)),
    ])
  }
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  load_status.render(
    model.load_status,
    fn() { loading_view(model.series_uid) },
    fn() {
      case dict.get(shared.cache.series, model.series_uid) {
        Ok(s) -> render_detail(s, shared.translate)
        Error(_) -> loading_view(model.series_uid)
      }
    },
    fn(msg) { error_view(msg) },
  )
}

fn render_detail(s: Series, translate: fn(Key) -> String) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Series: " <> s.series_uid)]),
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(NavigateBack(s.study_uid)),
        ],
        [html.text("Back to Study")],
      ),
    ]),
    series_info_card(s),
    parent_study_section(s),
    records_section(s.records, translate),
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
            attribute.href(
              router.route_to_path(router.StudyDetail(s.study_uid)),
            ),
            attribute.class("link"),
          ],
          [html.text(s.study_uid)],
        ),
      ]),
    ]),
    html.div([attribute.class("card-actions")], [
      viewer.viewer_button(
        Some(s.study_uid),
        Some(s.series_uid),
        "btn btn-primary",
      ),
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
                attribute.href(
                  router.route_to_path(router.StudyDetail(study.study_uid)),
                ),
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
                attribute.href(
                  router.route_to_path(router.PatientDetail(study.patient_id)),
                ),
                attribute.class("link"),
              ],
              [html.text(study.patient_id)],
            ),
          ]),
        ]),
      ])
  }
}

fn records_section(records: Option(List(Record)), translate: fn(Key) -> String) -> Element(Msg) {
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
            html.tbody([], list.map(record_list, record_row(_, translate))),
          ]),
        ])
    },
  ])
}

fn record_row(record: Record, translate: fn(Key) -> String) -> Element(Msg) {
  let record_id = option.unwrap(record.id, 0)
  let record_id_str = int.to_string(record_id)

  html.tr([], [
    html.td([], [html.text(record_id_str)]),
    html.td([], [html.text(record.record_type_name)]),
    html.td([], [status_badge.render(record.status, translate)]),
    html.td([], [html.text(record.patient_id)]),
    html.td([], [
      html.a(
        [
          attribute.href(
            router.route_to_path(router.RecordDetail(record_id_str)),
          ),
          attribute.class("btn btn-sm btn-outline"),
        ],
        [html.text("View")],
      ),
    ]),
  ])
}

fn loading_view(series_uid: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading series " <> series_uid <> "...")]),
  ])
}

fn error_view(message: String) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
    html.button(
      [attribute.class("btn btn-primary"), event.on_click(RetryLoad)],
      [html.text("Retry")],
    ),
  ])
}
