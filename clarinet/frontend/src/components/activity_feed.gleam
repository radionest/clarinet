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
import components/forms/base
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

/// In-memory filters for the global feed. Ignored for record/patient sources
/// (those have no filter UI). An empty string means "no filter" on that field.
pub type Filters {
  Filters(
    event_kind: String,
    event_since: String,
    run_status: String,
    run_task_name: String,
    run_since: String,
  )
}

fn empty_filters() -> Filters {
  Filters(
    event_kind: "",
    event_since: "",
    run_status: "",
    run_task_name: "",
    run_since: "",
  )
}

// --- Model ---

pub type Model {
  Model(
    source: Source,
    tab: ActivityTab,
    filters: Filters,
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
  // Global-feed filters
  EventKindSelected(String)
  EventSinceChanged(String)
  RunStatusSelected(String)
  RunTaskNameChanged(String)
  RunSinceChanged(String)
}

/// The only signal the host must act on: a 401 during a load. Non-auth
/// failures are surfaced inline (the `Failed` LoadStatus + a retry button),
/// so they never escalate to the host.
pub type OutMsg {
  AuthExpired
}

// --- Init ---

pub fn init(source: Source) -> #(Model, Effect(Msg), List(OutMsg)) {
  let filters = empty_filters()
  let model =
    Model(
      source: source,
      tab: EventsTab,
      filters: filters,
      events: [],
      events_status: load_status.Loading,
      runs: [],
      runs_status: load_status.Loading,
    )
  #(
    model,
    effect.batch([load_events(source, filters), load_runs(source, filters)]),
    [],
  )
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
      load_events(model.source, model.filters),
      [],
    )
    Retry(RunsTab) -> #(
      Model(..model, runs_status: load_status.Loading),
      load_runs(model.source, model.filters),
      [],
    )

    EventKindSelected(kind) ->
      reload_events(model, Filters(..model.filters, event_kind: kind))
    EventSinceChanged(since) ->
      reload_events(model, Filters(..model.filters, event_since: since))
    RunStatusSelected(status) ->
      reload_runs(model, Filters(..model.filters, run_status: status))
    RunTaskNameChanged(name) ->
      reload_runs(model, Filters(..model.filters, run_task_name: name))
    RunSinceChanged(since) ->
      reload_runs(model, Filters(..model.filters, run_since: since))
  }
}

/// Apply new filters and refetch the events tab; the runs tab is untouched.
fn reload_events(
  model: Model,
  filters: Filters,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(..model, filters: filters, events_status: load_status.Loading)
  #(model, load_events(model.source, filters), [])
}

/// Apply new filters and refetch the runs tab; the events tab is untouched.
fn reload_runs(
  model: Model,
  filters: Filters,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model = Model(..model, filters: filters, runs_status: load_status.Loading)
  #(model, load_runs(model.source, filters), [])
}

/// Only a session-killing 401 escalates to the host; everything else is shown
/// inline via the `Failed` LoadStatus.
fn auth_out(err: ApiError) -> List(OutMsg) {
  case err {
    AuthError(_) -> [AuthExpired]
    _ -> []
  }
}

fn load_events(source: Source, filters: Filters) -> Effect(Msg) {
  use dispatch <- effect.from
  events_promise(source, filters)
  |> promise.tap(fn(result) { dispatch(EventsLoaded(result)) })
  Nil
}

fn events_promise(
  source: Source,
  filters: Filters,
) -> Promise(Result(List(RecordEvent), ApiError)) {
  case source {
    RecordSource(id) -> audit.get_record_events(id)
    PatientSource(id) -> audit.list_events(Some(id), None, None)
    GlobalSource ->
      audit.list_events(
        None,
        none_if_empty(filters.event_kind),
        none_if_empty(filters.event_since),
      )
  }
}

fn load_runs(source: Source, filters: Filters) -> Effect(Msg) {
  use dispatch <- effect.from
  runs_promise(source, filters)
  |> promise.tap(fn(result) { dispatch(RunsLoaded(result)) })
  Nil
}

fn runs_promise(
  source: Source,
  filters: Filters,
) -> Promise(Result(List(PipelineRun), ApiError)) {
  case source {
    RecordSource(id) -> audit.get_record_runs(id)
    PatientSource(id) -> audit.list_runs(Some(id), None, None, None)
    GlobalSource ->
      audit.list_runs(
        None,
        none_if_empty(filters.run_status),
        none_if_empty(filters.run_task_name),
        none_if_empty(filters.run_since),
      )
  }
}

fn none_if_empty(value: String) -> Option(String) {
  case value {
    "" -> None
    _ -> Some(value)
  }
}

// --- View ---

/// Tab bar + the active tab's table wrapped in the tri-state LoadStatus
/// renderer. The component dispatches its own `Msg`; the host maps the result
/// via `element.map(view(..), HostActivityMsg)`.
pub fn view(model: Model, t: fn(Key) -> String) -> Element(Msg) {
  html.div([attribute.class("activity")], [
    tabs(model.tab, t),
    filter_bar(model, t),
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

// --- Filter bar (global feed only) ---

/// Renders the filter row for the active tab — but only for the global feed.
/// Record/patient feeds are already scoped, so they show no filters.
fn filter_bar(model: Model, t: fn(Key) -> String) -> Element(Msg) {
  case model.source {
    GlobalSource ->
      case model.tab {
        EventsTab -> events_filter_bar(model.filters, t)
        RunsTab -> runs_filter_bar(model.filters, t)
      }
    _ -> element.none()
  }
}

fn events_filter_bar(filters: Filters, t: fn(Key) -> String) -> Element(Msg) {
  html.div([attribute.class("activity-filters")], [
    filter_field(
      t(i18n.ThEvent),
      base.select(
        name: "activity-event-kind",
        value: filters.event_kind,
        options: event_kind_options(t),
        on_change: EventKindSelected,
      ),
    ),
    filter_field(
      t(i18n.ActivityFilterSince),
      base.date_input(
        name: "activity-event-since",
        value: filters.event_since,
        on_input: EventSinceChanged,
      ),
    ),
  ])
}

fn runs_filter_bar(filters: Filters, t: fn(Key) -> String) -> Element(Msg) {
  html.div([attribute.class("activity-filters")], [
    filter_field(
      t(i18n.ThStatus),
      base.select(
        name: "activity-run-status",
        value: filters.run_status,
        options: run_status_options(t),
        on_change: RunStatusSelected,
      ),
    ),
    filter_field(
      t(i18n.ThTask),
      base.text_input(
        name: "activity-run-task",
        value: filters.run_task_name,
        placeholder: None,
        on_input: RunTaskNameChanged,
      ),
    ),
    filter_field(
      t(i18n.ActivityFilterSince),
      base.date_input(
        name: "activity-run-since",
        value: filters.run_since,
        on_input: RunSinceChanged,
      ),
    ),
  ])
}

fn filter_field(label: String, control: Element(Msg)) -> Element(Msg) {
  html.label([attribute.class("activity-filter")], [
    html.span([attribute.class("activity-filter-label")], [html.text(label)]),
    control,
  ])
}

fn event_kind_options(t: fn(Key) -> String) -> List(#(String, String)) {
  [
    #("", t(i18n.ActivityFilterAllKinds)),
    #("created", t(i18n.ActivityKindCreated)),
    #("status_changed", t(i18n.ActivityKindStatusChanged)),
    #("data_submitted", t(i18n.ActivityKindDataSubmitted)),
    #("data_updated", t(i18n.ActivityKindDataUpdated)),
    #("assigned", t(i18n.ActivityKindAssigned)),
    #("unassigned", t(i18n.ActivityKindUnassigned)),
    #("failed", t(i18n.ActivityKindFailed)),
    #("invalidated", t(i18n.ActivityKindInvalidated)),
    #("context_info_updated", t(i18n.ActivityKindContextInfoUpdated)),
    #("files_cleared", t(i18n.ActivityKindFilesCleared)),
    #("deleted", t(i18n.ActivityKindDeleted)),
  ]
}

fn run_status_options(t: fn(Key) -> String) -> List(#(String, String)) {
  [
    #("", t(i18n.ActivityFilterAllStatuses)),
    #("running", t(i18n.ActivityRunRunning)),
    #("succeeded", t(i18n.ActivityRunSucceeded)),
    #("failed", t(i18n.ActivityRunFailed)),
    #("retrying", t(i18n.ActivityRunRetrying)),
  ]
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
