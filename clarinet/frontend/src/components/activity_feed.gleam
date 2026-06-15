// Audit/activity feed — a self-contained MVU sub-component embedded by the
// per-record, per-patient and global-admin pages. It owns the active tab,
// the tri-state LoadStatus of both tabs, and the fetched data; the host page
// only stores `Model`, forwards `Msg`, and translates the lone `OutMsg`
// (`AuthExpired`, raised on a 401) into its own logout signal. The module
// deliberately does NOT import `shared` — like `cache`/`preload`, it keeps a
// minimal local `OutMsg` so it stays reusable across pages.
import api/audit
import api/models.{type PipelineRun, type RecordEvent}
import api/types.{type ApiError, AuthError}
import clarinet_frontend/i18n.{type Key}
import gleam/float
import gleam/int
import gleam/javascript/promise.{type Promise}
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import utils/datetime
import utils/load_status.{type LoadStatus}

pub type ActivityTab {
  EventsTab
  RunsTab
}

/// Selects which `api/audit` calls back the feed. Per-record uses the
/// record-scoped endpoints (visible to anyone with record access); per-patient
/// and global use the admin endpoints (`patient_id` filter vs. server-wide).
pub type Source {
  RecordSource(record_id: String)
  PatientSource(patient_id: String)
  GlobalSource
}

// --- Model ---

pub type Model {
  Model(
    source: Source,
    tab: ActivityTab,
    events: List(RecordEvent),
    events_status: LoadStatus,
    runs: List(PipelineRun),
    runs_status: LoadStatus,
  )
}

// --- Msg / OutMsg ---

pub type Msg {
  TabSelected(ActivityTab)
  EventsLoaded(Result(List(RecordEvent), ApiError))
  RunsLoaded(Result(List(PipelineRun), ApiError))
  Retry(ActivityTab)
}

/// The only signal the host must act on: a 401 during a load. Non-auth
/// failures are surfaced inline (the `Failed` LoadStatus + a retry button),
/// so they never escalate to the host.
pub type OutMsg {
  AuthExpired
}

// --- Init ---

pub fn init(source: Source) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      source: source,
      tab: EventsTab,
      events: [],
      events_status: load_status.Loading,
      runs: [],
      runs_status: load_status.Loading,
    )
  #(model, effect.batch([load_events(source), load_runs(source)]), [])
}

// --- Update ---

pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg)) {
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
      auth_out(err),
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
      auth_out(err),
    )

    Retry(EventsTab) -> #(
      Model(..model, events_status: load_status.Loading),
      load_events(model.source),
      [],
    )
    Retry(RunsTab) -> #(
      Model(..model, runs_status: load_status.Loading),
      load_runs(model.source),
      [],
    )
  }
}

/// Only a session-killing 401 escalates to the host; everything else is shown
/// inline via the `Failed` LoadStatus.
fn auth_out(err: ApiError) -> List(OutMsg) {
  case err {
    AuthError(_) -> [AuthExpired]
    _ -> []
  }
}

fn load_events(source: Source) -> Effect(Msg) {
  use dispatch <- effect.from
  events_promise(source)
  |> promise.tap(fn(result) { dispatch(EventsLoaded(result)) })
  Nil
}

fn events_promise(
  source: Source,
) -> Promise(Result(List(RecordEvent), ApiError)) {
  case source {
    RecordSource(id) -> audit.get_record_events(id)
    PatientSource(id) -> audit.list_events(Some(id))
    GlobalSource -> audit.list_events(None)
  }
}

fn load_runs(source: Source) -> Effect(Msg) {
  use dispatch <- effect.from
  runs_promise(source)
  |> promise.tap(fn(result) { dispatch(RunsLoaded(result)) })
  Nil
}

fn runs_promise(source: Source) -> Promise(Result(List(PipelineRun), ApiError)) {
  case source {
    RecordSource(id) -> audit.get_record_runs(id)
    PatientSource(id) -> audit.list_runs(Some(id))
    GlobalSource -> audit.list_runs(None)
  }
}

// --- View ---

/// Tab bar + the active tab's table wrapped in the tri-state LoadStatus
/// renderer. The component dispatches its own `Msg`; the host maps the result
/// via `element.map(view(..), HostActivityMsg)`.
pub fn view(model: Model, t: fn(Key) -> String) -> Element(Msg) {
  html.div([attribute.class("activity")], [
    tabs(model.tab, t),
    case model.tab {
      EventsTab ->
        load_status.render(
          model.events_status,
          fn() { loading_view(t) },
          fn() { events_table(model.events, t) },
          fn(m) { error_view(m, Retry(EventsTab), t) },
        )
      RunsTab ->
        load_status.render(
          model.runs_status,
          fn() { loading_view(t) },
          fn() { runs_table(model.runs, t) },
          fn(m) { error_view(m, Retry(RunsTab), t) },
        )
    },
  ])
}

fn tabs(active: ActivityTab, t: fn(Key) -> String) -> Element(Msg) {
  html.div([attribute.class("activity-tabs")], [
    tab_button(i18n.ActivityTabEvents, EventsTab, active, t),
    tab_button(i18n.ActivityTabRuns, RunsTab, active, t),
  ])
}

fn tab_button(
  label: Key,
  tab: ActivityTab,
  active: ActivityTab,
  t: fn(Key) -> String,
) -> Element(Msg) {
  let class = case tab == active {
    True -> "btn btn-primary"
    False -> "btn btn-outline"
  }
  html.button([attribute.class(class), event.on_click(TabSelected(tab))], [
    html.text(t(label)),
  ])
}

fn loading_view(t: fn(Key) -> String) -> Element(msg) {
  html.div([attribute.class("loading")], [
    html.p([], [html.text(t(i18n.ActivityLoading))]),
  ])
}

fn error_view(
  message: String,
  retry_msg: Msg,
  t: fn(Key) -> String,
) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
    html.button(
      [attribute.class("btn btn-primary"), event.on_click(retry_msg)],
      [html.text(t(i18n.BtnRetry))],
    ),
  ])
}

// --- Record events table ---

pub fn events_table(
  events: List(RecordEvent),
  t: fn(Key) -> String,
) -> Element(msg) {
  case events {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(t(i18n.ActivityNoEvents)),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text(t(i18n.ThDate))]),
              html.th([], [html.text(t(i18n.ThEvent))]),
              html.th([], [html.text(t(i18n.ThUser))]),
              html.th([], [html.text(t(i18n.ThChange))]),
              html.th([], [html.text(t(i18n.ThReason))]),
            ]),
          ]),
          html.tbody([], list.map(events, fn(ev) { event_row(ev, t) })),
        ]),
      ])
  }
}

fn event_row(ev: RecordEvent, t: fn(Key) -> String) -> Element(msg) {
  html.tr([], [
    html.td([], [html.text(datetime.format(ev.occurred_at))]),
    html.td([], [kind_badge(ev.kind, t)]),
    html.td([], [
      html.text(option.unwrap(ev.actor_name, t(i18n.ActivitySystemActor))),
    ]),
    html.td([], [status_transition(ev)]),
    html.td([attribute.class("text-muted")], [
      html.text(option.unwrap(ev.reason, "—")),
    ]),
  ])
}

fn status_transition(ev: RecordEvent) -> Element(msg) {
  case ev.from_status, ev.to_status {
    Some(from), Some(to) -> html.text(from <> " → " <> to)
    None, Some(to) -> html.text("→ " <> to)
    _, _ -> html.text("—")
  }
}

fn kind_badge(kind: String, t: fn(Key) -> String) -> Element(msg) {
  let #(label, color) = kind_display(kind)
  html.span([attribute.class("badge badge-" <> color)], [html.text(t(label))])
}

/// Maps a backend `RecordEventKind` to its localized label key + a semantic
/// badge colour. Exhaustive over the known kinds; unknown strings fall back.
fn kind_display(kind: String) -> #(Key, String) {
  case kind {
    "created" -> #(i18n.ActivityKindCreated, "info")
    "status_changed" -> #(i18n.ActivityKindStatusChanged, "info")
    "data_submitted" -> #(i18n.ActivityKindDataSubmitted, "success")
    "data_updated" -> #(i18n.ActivityKindDataUpdated, "info")
    "assigned" -> #(i18n.ActivityKindAssigned, "info")
    "unassigned" -> #(i18n.ActivityKindUnassigned, "muted")
    "failed" -> #(i18n.ActivityKindFailed, "danger")
    "invalidated" -> #(i18n.ActivityKindInvalidated, "muted")
    "context_info_updated" -> #(i18n.ActivityKindContextInfoUpdated, "muted")
    "files_cleared" -> #(i18n.ActivityKindFilesCleared, "muted")
    "deleted" -> #(i18n.ActivityKindDeleted, "danger")
    _ -> #(i18n.ActivityKindOther, "muted")
  }
}

// --- Pipeline runs table ---

pub fn runs_table(runs: List(PipelineRun), t: fn(Key) -> String) -> Element(msg) {
  case runs {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(t(i18n.ActivityNoRuns)),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text(t(i18n.ThTask))]),
              html.th([], [html.text(t(i18n.ThStatus))]),
              html.th([], [html.text(t(i18n.ThStarted))]),
              html.th([], [html.text(t(i18n.ThDuration))]),
              html.th([], [html.text(t(i18n.ThError))]),
            ]),
          ]),
          html.tbody([], list.map(runs, fn(r) { run_row(r, t) })),
        ]),
      ])
  }
}

fn run_row(run: PipelineRun, t: fn(Key) -> String) -> Element(msg) {
  html.tr([], [
    html.td([], [html.text(run.task_name)]),
    html.td([], [run_status_badge(run.status, t)]),
    html.td([], [html.text(datetime.format(run.started_at))]),
    html.td([], [html.text(duration_text(run.execution_time))]),
    html.td([attribute.class("text-muted")], [html.text(error_text(run))]),
  ])
}

fn run_status_badge(status: String, t: fn(Key) -> String) -> Element(msg) {
  let #(label, color) = run_status_display(status)
  html.span([attribute.class("badge badge-" <> color)], [html.text(t(label))])
}

fn run_status_display(status: String) -> #(Key, String) {
  case status {
    "running" -> #(i18n.ActivityRunRunning, "info")
    "succeeded" -> #(i18n.ActivityRunSucceeded, "success")
    "failed" -> #(i18n.ActivityRunFailed, "danger")
    "retrying" -> #(i18n.ActivityRunRetrying, "info")
    _ -> #(i18n.ActivityRunOther, "muted")
  }
}

fn duration_text(secs: Option(Float)) -> String {
  case secs {
    Some(s) -> float.to_string(round2(s)) <> " s"
    None -> "—"
  }
}

fn round2(x: Float) -> Float {
  int.to_float(float.round(x *. 100.0)) /. 100.0
}

fn error_text(run: PipelineRun) -> String {
  case run.error_message, run.error_type {
    Some(message), _ -> message
    None, Some(ty) -> ty
    None, None -> "—"
  }
}
