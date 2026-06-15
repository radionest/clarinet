// Admin Activity page — server-wide audit feed (record events + pipeline runs).
import api/audit
import api/models.{type PipelineRun, type RecordEvent}
import api/types.{type ApiError}
import clarinet_frontend/i18n
import components/activity_feed.{type ActivityTab, EventsTab, RunsTab}
import gleam/javascript/promise
import gleam/option.{None}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}

// --- Model ---

pub type Model {
  Model(
    tab: ActivityTab,
    events: List(RecordEvent),
    events_status: LoadStatus,
    runs: List(PipelineRun),
    runs_status: LoadStatus,
  )
}

// --- Msg ---

pub type Msg {
  TabSelected(ActivityTab)
  EventsLoaded(Result(List(RecordEvent), ApiError))
  RunsLoaded(Result(List(PipelineRun), ApiError))
  Retry(ActivityTab)
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      tab: EventsTab,
      events: [],
      events_status: load_status.Loading,
      runs: [],
      runs_status: load_status.Loading,
    )
  #(model, effect.batch([load_events(), load_runs()]), [])
}

fn load_events() -> Effect(Msg) {
  use dispatch <- effect.from
  audit.list_events(None)
  |> promise.tap(fn(result) { dispatch(EventsLoaded(result)) })
  Nil
}

fn load_runs() -> Effect(Msg) {
  use dispatch <- effect.from
  audit.list_runs(None)
  |> promise.tap(fn(result) { dispatch(RunsLoaded(result)) })
  Nil
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    TabSelected(tab) -> #(Model(..model, tab: tab), effect.none(), [])

    EventsLoaded(Ok(events)) -> #(
      Model(..model, events: events, events_status: load_status.Loaded),
      effect.none(),
      [],
    )
    EventsLoaded(Error(err)) -> #(
      Model(..model, events_status: load_status.Failed("Failed to load events")),
      effect.none(),
      handle_error(err, "Failed to load events"),
    )

    RunsLoaded(Ok(runs)) -> #(
      Model(..model, runs: runs, runs_status: load_status.Loaded),
      effect.none(),
      [],
    )
    RunsLoaded(Error(err)) -> #(
      Model(
        ..model,
        runs_status: load_status.Failed("Failed to load pipeline runs"),
      ),
      effect.none(),
      handle_error(err, "Failed to load pipeline runs"),
    )

    Retry(EventsTab) -> #(
      Model(..model, events_status: load_status.Loading),
      load_events(),
      [],
    )
    Retry(RunsTab) -> #(
      Model(..model, runs_status: load_status.Loading),
      load_runs(),
      [],
    )
  }
}

fn handle_error(err: ApiError, fallback: String) -> List(OutMsg) {
  case err {
    types.AuthError(_) -> [shared.Logout]
    _ -> [shared.ShowError(fallback)]
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text(shared.translate(i18n.NavActivity))]),
    ]),
    activity_feed.view(
      model.tab,
      model.events_status,
      model.events,
      model.runs_status,
      model.runs,
      TabSelected,
      Retry,
      shared.translate,
    ),
  ])
}
