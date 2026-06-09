// Admin Quarto Reports page — list of *.qmd reports with background render +
// per-format download. Rendering is asynchronous: clicking "Render DOCX" starts
// a server-side job; the page polls its status and swaps in a download link
// once it finishes.
import api/models.{type QuartoReportTemplate, type QuartoRenderState}
import api/quarto_reports as quarto_api
import api/types.{type ApiError}
import gleam/dict.{type Dict}
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import plinth/javascript/global
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}

// --- Model ---

// One render job for a (report, format) pair. ``render_id`` is empty until the
// POST returns; ``status`` mirrors the backend sidecar (pending/running/done/failed).
pub type RenderEntry {
  RenderEntry(
    name: String,
    format: String,
    render_id: String,
    status: String,
    error: Option(String),
  )
}

pub type Model {
  Model(
    reports: List(QuartoReportTemplate),
    load_status: LoadStatus,
    // keyed by `render_key(name, format)`
    renders: Dict(String, RenderEntry),
    // whether a poll timer chain is currently running (avoids duplicate timers)
    polling: Bool,
  )
}

// --- Msg ---

pub type Msg {
  ReportsLoaded(Result(List(QuartoReportTemplate), ApiError))
  RetryLoad
  TriggerRender(name: String, format: String)
  RenderTriggered(key: String, result: Result(QuartoRenderState, ApiError))
  PollTick
  RenderPolled(key: String, result: Result(QuartoRenderState, ApiError))
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      reports: [],
      load_status: load_status.Loading,
      renders: dict.new(),
      polling: False,
    )
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
      Model(..model, reports: reports, load_status: load_status.Loaded),
      effect.none(),
      [],
    )
    ReportsLoaded(Error(err)) -> #(
      Model(
        ..model,
        load_status: load_status.Failed("Failed to load Quarto reports"),
      ),
      effect.none(),
      handle_error(err, "Failed to load Quarto reports"),
    )
    RetryLoad -> #(
      Model(..model, load_status: load_status.Loading),
      load_reports_effect(),
      [],
    )
    TriggerRender(name, format) -> {
      let key = render_key(name, format)
      case dict.get(model.renders, key) {
        // Ignore a repeat click while a render for this format is still in
        // flight — otherwise a second POST leaks a duplicate server render.
        Ok(entry) if entry.status == "pending" || entry.status == "running" -> #(
          model,
          effect.none(),
          [],
        )
        _ -> {
          let entry =
            RenderEntry(
              name: name,
              format: format,
              render_id: "",
              status: "pending",
              error: None,
            )
          #(
            Model(..model, renders: dict.insert(model.renders, key, entry)),
            trigger_render_effect(name, format, key),
            [],
          )
        }
      }
    }
    RenderTriggered(key, Ok(state)) -> {
      let #(polling, poll) = ensure_polling(model.polling)
      #(
        Model(
          ..model,
          renders: update_entry(model.renders, key, state),
          polling: polling,
        ),
        poll,
        [],
      )
    }
    RenderTriggered(key, Error(err)) -> #(
      Model(..model, renders: mark_failed(model.renders, key)),
      effect.none(),
      handle_error(err, "Failed to start render"),
    )
    PollTick ->
      case active_entries(model.renders) {
        [] -> #(Model(..model, polling: False), effect.none(), [])
        active -> {
          let polls =
            list.map(active, fn(pair) {
              let #(key, entry) = pair
              poll_effect(entry.name, entry.render_id, key)
            })
          #(
            Model(..model, polling: True),
            effect.batch([schedule_poll(), ..polls]),
            [],
          )
        }
      }
    RenderPolled(key, Ok(state)) -> #(
      Model(..model, renders: update_entry(model.renders, key, state)),
      effect.none(),
      [],
    )
    RenderPolled(key, Error(err)) -> #(
      Model(..model, renders: mark_failed(model.renders, key)),
      effect.none(),
      handle_error(err, "Failed to poll render status"),
    )
  }
}

fn render_key(name: String, format: String) -> String {
  name <> "|" <> format
}

fn update_entry(
  renders: Dict(String, RenderEntry),
  key: String,
  state: QuartoRenderState,
) -> Dict(String, RenderEntry) {
  case dict.get(renders, key) {
    Ok(entry) ->
      dict.insert(
        renders,
        key,
        RenderEntry(
          ..entry,
          render_id: state.render_id,
          status: state.status,
          error: state.error,
        ),
      )
    Error(_) -> renders
  }
}

fn mark_failed(
  renders: Dict(String, RenderEntry),
  key: String,
) -> Dict(String, RenderEntry) {
  case dict.get(renders, key) {
    Ok(entry) ->
      dict.insert(
        renders,
        key,
        RenderEntry(..entry, status: "failed", error: Some("Request failed")),
      )
    Error(_) -> renders
  }
}

// Renders that can still change (and have a render_id worth polling).
fn active_entries(
  renders: Dict(String, RenderEntry),
) -> List(#(String, RenderEntry)) {
  dict.to_list(renders)
  |> list.filter(fn(pair) {
    let #(_, entry) = pair
    entry.render_id != ""
    && { entry.status == "pending" || entry.status == "running" }
  })
}

// Arm the poll timer only when no chain is already running. The chain
// self-terminates: a PollTick with no active renders stops re-arming, and a
// stray tick after navigating away is dropped by delegate_page_update.
fn ensure_polling(polling: Bool) -> #(Bool, Effect(Msg)) {
  case polling {
    True -> #(True, effect.none())
    False -> #(True, schedule_poll())
  }
}

// --- Effects ---

fn load_reports_effect() -> Effect(Msg) {
  use dispatch <- effect.from
  quarto_api.list_quarto_reports()
  |> promise.tap(fn(result) { dispatch(ReportsLoaded(result)) })
  Nil
}

fn trigger_render_effect(name: String, format: String, key: String) -> Effect(Msg) {
  use dispatch <- effect.from
  quarto_api.render_report(name, [format])
  |> promise.tap(fn(result) { dispatch(RenderTriggered(key, result)) })
  Nil
}

fn poll_effect(name: String, render_id: String, key: String) -> Effect(Msg) {
  use dispatch <- effect.from
  quarto_api.get_render_status(name, render_id)
  |> promise.tap(fn(result) { dispatch(RenderPolled(key, result)) })
  Nil
}

fn schedule_poll() -> Effect(Msg) {
  use dispatch <- effect.from
  let _ = global.set_timeout(3000, fn() { dispatch(PollTick) })
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
      html.h1([], [html.text("Quarto Reports")]),
    ]),
    load_status.render(
      model.load_status,
      fn() { loading_view() },
      fn() { reports_view(model) },
      fn(msg) { error_view(msg) },
    ),
  ])
}

fn loading_view() -> Element(Msg) {
  html.div([attribute.class("loading")], [
    html.p([], [html.text("Loading Quarto reports...")]),
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

fn reports_view(model: Model) -> Element(Msg) {
  case model.reports {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(
          "No Quarto reports available. Add *.qmd files to the project's review/ folder.",
        ),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text("Title")]),
              html.th([], [html.text("Description")]),
              html.th([], [html.text("Data")]),
              html.th([], [html.text("Render")]),
            ]),
          ]),
          html.tbody([], list.map(model.reports, fn(r) { report_row(model, r) })),
        ]),
      ])
  }
}

fn report_row(model: Model, report: QuartoReportTemplate) -> Element(Msg) {
  html.tr([], [
    html.td([], [html.text(report.title)]),
    html.td([attribute.class("text-muted")], [html.text(report.description)]),
    html.td([attribute.class("text-muted")], [
      html.text(string.join(report.data_reports, ", ")),
    ]),
    html.td([attribute.class("report-actions")], [
      format_action(model, report.name, "docx", "DOCX"),
      html.text(" "),
      format_action(model, report.name, "pdf", "PDF"),
    ]),
  ])
}

fn format_action(
  model: Model,
  name: String,
  format: String,
  label: String,
) -> Element(Msg) {
  case dict.get(model.renders, render_key(name, format)) {
    Error(_) -> render_button(name, format, label)
    Ok(entry) ->
      case entry.status {
        "done" ->
          download_link(name, entry.render_id, format, "Download " <> label)
        "failed" -> render_failed(name, format, label, entry.error)
        _ -> rendering_indicator(label)
      }
  }
}

fn render_button(name: String, format: String, label: String) -> Element(Msg) {
  html.button(
    [
      attribute.class("btn btn-sm btn-outline"),
      event.on_click(TriggerRender(name, format)),
    ],
    [html.text("Render " <> label)],
  )
}

fn rendering_indicator(label: String) -> Element(Msg) {
  html.span([attribute.class("text-muted")], [
    html.text("Rendering " <> label <> "…"),
  ])
}

fn render_failed(
  name: String,
  format: String,
  label: String,
  error: Option(String),
) -> Element(Msg) {
  let tooltip = case error {
    Some(msg) -> msg
    None -> "Render failed"
  }
  html.button(
    [
      attribute.class("btn btn-sm btn-danger"),
      attribute.title(tooltip),
      event.on_click(TriggerRender(name, format)),
    ],
    [html.text("Retry " <> label)],
  )
}

fn download_link(
  name: String,
  render_id: String,
  format: String,
  label: String,
) -> Element(Msg) {
  // target="_blank" — modem's global click handler treats this anchor as
  // external and skips preventDefault, letting the browser perform the native
  // download. Without it, modem routes the API URL through the SPA router,
  // which renders 404. The `download` attribute keeps the file saving.
  html.a(
    [
      attribute.class("btn btn-sm btn-outline"),
      attribute.href(quarto_api.download_url(name, render_id, format)),
      attribute.target("_blank"),
      attribute.attribute("download", ""),
    ],
    [html.text(label)],
  )
}
