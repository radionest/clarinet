// Admin Reports page — list of available SQL reports with CSV/XLSX download links.
import api/models.{type ReportTemplate}
import api/reports as reports_api
import api/types.{type ApiError}
import gleam/javascript/promise
import gleam/list
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}

// --- Model ---

pub type Model {
  Model(reports: List(ReportTemplate), load_status: LoadStatus)
}

// --- Msg ---

pub type Msg {
  ReportsLoaded(Result(List(ReportTemplate), ApiError))
  RetryLoad
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model = Model(reports: [], load_status: load_status.Loading)
  #(model, load_reports_effect(), [])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    ReportsLoaded(Ok(reports)) -> #(
      Model(reports: reports, load_status: load_status.Loaded),
      effect.none(),
      [],
    )
    ReportsLoaded(Error(err)) -> #(
      Model(..model, load_status: load_status.Failed("Failed to load reports")),
      effect.none(),
      handle_error(err, "Failed to load reports"),
    )
    RetryLoad -> #(
      Model(..model, load_status: load_status.Loading),
      load_reports_effect(),
      [],
    )
  }
}

fn load_reports_effect() -> Effect(Msg) {
  use dispatch <- effect.from
  reports_api.list_reports()
  |> promise.tap(fn(result) { dispatch(ReportsLoaded(result)) })
  Nil
}

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    types.AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, _shared: Shared) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Reports")]),
    ]),
    load_status.render(
      model.load_status,
      fn() { loading_view() },
      fn() { reports_view(model.reports) },
      fn(msg) { error_view(msg) },
    ),
  ])
}

fn loading_view() -> Element(Msg) {
  html.div([attribute.class("loading")], [
    html.p([], [html.text("Loading reports...")]),
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

fn reports_view(reports: List(ReportTemplate)) -> Element(Msg) {
  case reports {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(
          "No reports available. Add *.sql files to the project's review/ folder.",
        ),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text("Title")]),
              html.th([], [html.text("Description")]),
              html.th([], [html.text("Download")]),
            ]),
          ]),
          html.tbody([], list.map(reports, report_row)),
        ]),
      ])
  }
}

fn report_row(report: ReportTemplate) -> Element(Msg) {
  html.tr([], [
    html.td([], [html.text(report.title)]),
    html.td([attribute.class("text-muted")], [html.text(report.description)]),
    html.td([attribute.class("report-actions")], [
      download_link(report.name, "csv", "CSV"),
      html.text(" "),
      download_link(report.name, "xlsx", "XLSX"),
    ]),
  ])
}

fn download_link(name: String, format: String, label: String) -> Element(Msg) {
  html.a(
    [
      attribute.class("btn btn-sm btn-outline"),
      attribute.href(reports_api.download_url(name, format)),
      attribute.attribute("download", ""),
    ],
    [html.text(label)],
  )
}
