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
import gleam/set.{type Set}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/element/keyed
import lustre/event
import plinth/javascript/global
import router
import utils/datetime
import utils/load_status.{type LoadStatus}

// Throttle window for SSE-driven silent refetches of the active tab: a burst of
// pushes coalesces into a single refetch, capping the refresh rate to ~1/s.
const refresh_throttle_ms = 1000

// How long a freshly-arrived row keeps its highlight before the flash clears.
const highlight_window_ms = 2500

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
    event_actor: String,
    event_record_type: String,
    event_since: String,
    run_status: String,
    run_task_name: String,
    run_since: String,
  )
}

fn empty_filters() -> Filters {
  Filters(
    event_kind: "",
    event_actor: "",
    event_record_type: "",
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
    // Monotonic load tokens — a fetch result is applied only when its token
    // still matches, so a slow stale response can't clobber a fresher one
    // (rapid filter changes / typing fire overlapping requests).
    events_gen: Int,
    runs_gen: Int,
    // SSE live-refresh state (armed only for the global admin feed — the lone
    // source main.delegate_sse routes pushes to; record/patient feeds keep the
    // timers None and the highlight sets empty). `refresh_timer` throttles the
    // silent refetch; `new_*_ids` are the rows that just arrived (highlighted);
    // `highlight_timer` clears that highlight after a short window.
    refresh_timer: Option(global.TimerID),
    new_event_ids: Set(Int),
    new_run_ids: Set(String),
    highlight_timer: Option(global.TimerID),
  )
}

// --- Msg / OutMsg ---

pub type Msg {
  TabSelected(ActivityTab)
  EventsLoaded(gen: Int, result: Result(List(RecordEvent), ApiError))
  RunsLoaded(gen: Int, result: Result(List(PipelineRun), ApiError))
  Retry(ActivityTab)
  // Global-feed filters
  EventKindSelected(String)
  EventActorSelected(String)
  EventRecordTypeSelected(String)
  EventSinceChanged(String)
  RunStatusSelected(String)
  RunTaskNameChanged(String)
  RunSinceChanged(String)
  // SSE live refresh (global feed) — `External*Change` is dispatched by
  // main.delegate_sse on a relevant push; the rest drive the throttle and the
  // highlight-clear timers (cf. the watchdog pattern in sse.gleam).
  ExternalEventsChange
  ExternalRunsChange
  SetRefreshTimer(global.TimerID)
  RefreshTick(ActivityTab)
  SetHighlightTimer(global.TimerID)
  ClearHighlight
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
      events_gen: 1,
      runs_gen: 1,
      refresh_timer: None,
      new_event_ids: set.new(),
      new_run_ids: set.new(),
      highlight_timer: None,
    )
  #(
    model,
    effect.batch([
      load_events(source, filters, 1),
      load_runs(source, filters, 1),
    ]),
    [],
  )
}

// --- Update ---

pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    TabSelected(tab) ->
      case model.source {
        // The live admin feed: cancel any queued refresh/highlight, switch, and
        // silently refetch the now-visible tab so it reflects changes that
        // landed while it was hidden. Capture the timers to cancel *before*
        // `silent_refresh` nils the fields, or the old JS timeouts would leak
        // (a stale ClearHighlight could even cut a fresh highlight short).
        GlobalSource -> {
          let cancel =
            effect.batch([
              cancel_timer(model.refresh_timer),
              cancel_timer(model.highlight_timer),
            ])
          let #(model, eff, out) =
            silent_refresh(
              Model(
                ..model,
                tab: tab,
                refresh_timer: None,
                highlight_timer: None,
                new_event_ids: set.new(),
                new_run_ids: set.new(),
              ),
              tab,
            )
          #(model, effect.batch([cancel, eff]), out)
        }
        // Record/patient feeds aren't wired to SSE (main.delegate_sse only
        // routes to the admin page), so they never arm a timer — keep the
        // original instant, fetch-free switch.
        _ -> #(Model(..model, tab: tab), effect.none(), [])
      }

    EventsLoaded(gen, result) ->
      case gen == model.events_gen {
        False -> #(model, effect.none(), [])
        True -> apply_events(model, result)
      }

    RunsLoaded(gen, result) ->
      case gen == model.runs_gen {
        False -> #(model, effect.none(), [])
        True -> apply_runs(model, result)
      }

    Retry(EventsTab) -> {
      let gen = model.events_gen + 1
      #(
        Model(..model, events_status: load_status.Loading, events_gen: gen),
        load_events(model.source, model.filters, gen),
        [],
      )
    }
    Retry(RunsTab) -> {
      let gen = model.runs_gen + 1
      #(
        Model(..model, runs_status: load_status.Loading, runs_gen: gen),
        load_runs(model.source, model.filters, gen),
        [],
      )
    }

    EventKindSelected(kind) ->
      reload_events(model, Filters(..model.filters, event_kind: kind))
    EventActorSelected(actor) ->
      reload_events(model, Filters(..model.filters, event_actor: actor))
    EventRecordTypeSelected(record_type) ->
      reload_events(
        model,
        Filters(..model.filters, event_record_type: record_type),
      )
    EventSinceChanged(since) ->
      reload_events(model, Filters(..model.filters, event_since: since))
    RunStatusSelected(status) ->
      reload_runs(model, Filters(..model.filters, run_status: status))
    RunTaskNameChanged(name) ->
      reload_runs(model, Filters(..model.filters, run_task_name: name))
    RunSinceChanged(since) ->
      reload_runs(model, Filters(..model.filters, run_since: since))

    // --- SSE live refresh ---
    ExternalEventsChange -> schedule_refresh(model, EventsTab)
    ExternalRunsChange -> schedule_refresh(model, RunsTab)
    SetRefreshTimer(id) -> #(
      Model(..model, refresh_timer: Some(id)),
      effect.none(),
      [],
    )
    RefreshTick(tab) -> silent_refresh(Model(..model, refresh_timer: None), tab)
    SetHighlightTimer(id) -> #(
      Model(..model, highlight_timer: Some(id)),
      effect.none(),
      [],
    )
    ClearHighlight -> #(
      Model(
        ..model,
        new_event_ids: set.new(),
        new_run_ids: set.new(),
        highlight_timer: None,
      ),
      effect.none(),
      [],
    )
  }
}

/// Apply new filters and refetch the events tab; the runs tab is untouched.
fn reload_events(
  model: Model,
  filters: Filters,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let gen = model.events_gen + 1
  let model =
    Model(
      ..model,
      filters: filters,
      events_status: load_status.Loading,
      events_gen: gen,
      new_event_ids: set.new(),
    )
  #(model, load_events(model.source, filters, gen), [])
}

/// Apply new filters and refetch the runs tab; the events tab is untouched.
fn reload_runs(
  model: Model,
  filters: Filters,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let gen = model.runs_gen + 1
  let model =
    Model(
      ..model,
      filters: filters,
      runs_status: load_status.Loading,
      runs_gen: gen,
      new_run_ids: set.new(),
    )
  #(model, load_runs(model.source, filters, gen), [])
}

/// Only a session-killing 401 escalates to the host; everything else is shown
/// inline via the `Failed` LoadStatus.
fn auth_out(err: ApiError) -> List(OutMsg) {
  case err {
    AuthError(_) -> [AuthExpired]
    _ -> []
  }
}

// --- SSE live refresh ---

/// Apply an events fetch. The initial / filter / retry load (status `Loading`)
/// just shows the data; a silent SSE refetch (status already `Loaded`) keeps the
/// table on screen, highlights the rows that just appeared, and on failure
/// leaves the current rows untouched — only a 401 ever escalates.
fn apply_events(
  model: Model,
  result: Result(List(RecordEvent), ApiError),
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let silent = model.events_status == load_status.Loaded
  case result, silent {
    Ok(events), True -> {
      let added = added_ids(model.events, events, fn(e) { e.id })
      case set.is_empty(added) {
        True -> #(Model(..model, events: events), effect.none(), [])
        False -> #(
          Model(..model, events: events, new_event_ids: added),
          arm_highlight(model.highlight_timer),
          [],
        )
      }
    }
    Ok(events), False -> #(
      Model(..model, events: events, events_status: load_status.Loaded),
      effect.none(),
      [],
    )
    Error(err), True -> #(model, effect.none(), auth_out(err))
    Error(err), False -> #(
      Model(..model, events_status: load_status.Failed("Failed to load events")),
      effect.none(),
      auth_out(err),
    )
  }
}

/// Runs counterpart of `apply_events`.
fn apply_runs(
  model: Model,
  result: Result(List(PipelineRun), ApiError),
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let silent = model.runs_status == load_status.Loaded
  case result, silent {
    Ok(runs), True -> {
      let added = added_ids(model.runs, runs, fn(r) { r.id })
      case set.is_empty(added) {
        True -> #(Model(..model, runs: runs), effect.none(), [])
        False -> #(
          Model(..model, runs: runs, new_run_ids: added),
          arm_highlight(model.highlight_timer),
          [],
        )
      }
    }
    Ok(runs), False -> #(
      Model(..model, runs: runs, runs_status: load_status.Loaded),
      effect.none(),
      [],
    )
    Error(err), True -> #(model, effect.none(), auth_out(err))
    Error(err), False -> #(
      Model(
        ..model,
        runs_status: load_status.Failed("Failed to load pipeline runs"),
      ),
      effect.none(),
      auth_out(err),
    )
  }
}

/// Schedule a throttled silent refetch of `tab`. Only the visible tab
/// auto-refreshes (the other is refetched on `TabSelected`); while a refresh is
/// already queued, further pushes are dropped — the queued one catches them.
fn schedule_refresh(
  model: Model,
  tab: ActivityTab,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case tab == model.tab, model.refresh_timer {
    True, None -> #(model, arm_refresh(tab), [])
    _, _ -> #(model, effect.none(), [])
  }
}

/// Refetch a tab with the current filters without flipping it to `Loading`, so
/// the table stays on screen and the rows swap in place when the result lands.
fn silent_refresh(
  model: Model,
  tab: ActivityTab,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case tab {
    EventsTab -> {
      let gen = model.events_gen + 1
      #(
        Model(..model, events_gen: gen),
        load_events(model.source, model.filters, gen),
        [],
      )
    }
    RunsTab -> {
      let gen = model.runs_gen + 1
      #(
        Model(..model, runs_gen: gen),
        load_runs(model.source, model.filters, gen),
        [],
      )
    }
  }
}

fn arm_refresh(tab: ActivityTab) -> Effect(Msg) {
  use dispatch <- effect.from
  let id =
    global.set_timeout(refresh_throttle_ms, fn() { dispatch(RefreshTick(tab)) })
  dispatch(SetRefreshTimer(id))
}

/// (Re)arm the highlight-clear timer, cancelling any pending one so a fresh
/// burst extends the window instead of an old timer cutting it short.
fn arm_highlight(old: Option(global.TimerID)) -> Effect(Msg) {
  use dispatch <- effect.from
  case old {
    Some(id) -> global.clear_timeout(id)
    None -> Nil
  }
  let id =
    global.set_timeout(highlight_window_ms, fn() { dispatch(ClearHighlight) })
  dispatch(SetHighlightTimer(id))
}

/// Keys present in `new` but not in `old` — the rows that just appeared.
fn added_ids(old: List(a), new: List(a), key: fn(a) -> b) -> Set(b) {
  let seen = old |> list.map(key) |> set.from_list
  new
  |> list.map(key)
  |> list.filter(fn(k) { !set.contains(seen, k) })
  |> set.from_list
}

/// Cancel pending refresh / highlight timers on route change so a queued
/// refetch can't fire into a stale page. Wired via the host page's `cleanup`.
pub fn cleanup(model: Model) -> Effect(Msg) {
  effect.batch([
    cancel_timer(model.refresh_timer),
    cancel_timer(model.highlight_timer),
  ])
}

fn cancel_timer(timer: Option(global.TimerID)) -> Effect(Msg) {
  case timer {
    Some(id) -> effect.from(fn(_dispatch) { global.clear_timeout(id) })
    None -> effect.none()
  }
}

fn load_events(source: Source, filters: Filters, gen: Int) -> Effect(Msg) {
  use dispatch <- effect.from
  events_promise(source, filters)
  |> promise.tap(fn(result) { dispatch(EventsLoaded(gen, result)) })
  Nil
}

fn events_promise(
  source: Source,
  filters: Filters,
) -> Promise(Result(List(RecordEvent), ApiError)) {
  case source {
    RecordSource(id) -> audit.get_record_events(id)
    PatientSource(id) -> audit.list_events(Some(id), None, None, None, None)
    GlobalSource ->
      audit.list_events(
        None,
        none_if_empty(filters.event_kind),
        none_if_empty(filters.event_actor),
        none_if_empty(filters.event_record_type),
        none_if_empty(filters.event_since),
      )
  }
}

fn load_runs(source: Source, filters: Filters, gen: Int) -> Effect(Msg) {
  use dispatch <- effect.from
  runs_promise(source, filters)
  |> promise.tap(fn(result) { dispatch(RunsLoaded(gen, result)) })
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
pub fn view(
  model: Model,
  t: fn(Key) -> String,
  actor_options: List(#(String, String)),
  record_type_options: List(#(String, String)),
) -> Element(Msg) {
  html.div([attribute.class("activity")], [
    tabs(model.tab, t),
    filter_bar(model, actor_options, record_type_options, t),
    case model.tab {
      EventsTab ->
        load_status.render(
          model.events_status,
          fn() { loading_view(t) },
          fn() { events_table(model.events, model.source, model.new_event_ids, t) },
          fn(m) { error_view(m, Retry(EventsTab), t) },
        )
      RunsTab ->
        load_status.render(
          model.runs_status,
          fn() { loading_view(t) },
          fn() { runs_table(model.runs, model.source, model.new_run_ids, t) },
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
    True -> "activity-tab is-active"
    False -> "activity-tab"
  }
  html.button([attribute.class(class), event.on_click(TabSelected(tab))], [
    html.text(t(label)),
  ])
}

// --- Filter bar (global feed only) ---

/// Renders the filter row for the active tab — but only for the global feed.
/// Record/patient feeds are already scoped, so they show no filters.
fn filter_bar(
  model: Model,
  actor_options: List(#(String, String)),
  record_type_options: List(#(String, String)),
  t: fn(Key) -> String,
) -> Element(Msg) {
  case model.source {
    GlobalSource ->
      case model.tab {
        EventsTab ->
          events_filter_bar(model.filters, actor_options, record_type_options, t)
        RunsTab -> runs_filter_bar(model.filters, t)
      }
    _ -> element.none()
  }
}

fn events_filter_bar(
  filters: Filters,
  actor_options: List(#(String, String)),
  record_type_options: List(#(String, String)),
  t: fn(Key) -> String,
) -> Element(Msg) {
  html.div([attribute.class("activity-filters")], [
    filter_field(
      t(i18n.ActivityFilterKind),
      base.select(
        name: "activity-event-kind",
        value: filters.event_kind,
        options: event_kind_options(t),
        on_change: EventKindSelected,
      ),
    ),
    filter_field(
      t(i18n.ThUser),
      base.select(
        name: "activity-event-actor",
        value: filters.event_actor,
        options: actor_options,
        on_change: EventActorSelected,
      ),
    ),
    filter_field(
      t(i18n.ThRecordType),
      base.select(
        name: "activity-event-record-type",
        value: filters.event_record_type,
        options: record_type_options,
        on_change: EventRecordTypeSelected,
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
      // `on_change` (commit on blur / Enter) rather than `on_input` so typing a
      // task name doesn't fire a request on every keystroke.
      html.input([
        attribute.type_("text"),
        attribute.id("activity-run-task"),
        attribute.name("activity-run-task"),
        attribute.value(filters.run_task_name),
        attribute.class("form-input"),
        event.on_change(RunTaskNameChanged),
      ]),
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

// --- Shared audit cells (record + patient links) ---

/// Record column for a pipeline run (only a live `record_id` is available).
fn run_record_cell(record_id: Option(Int)) -> Element(msg) {
  case record_id {
    Some(id) -> record_link(id)
    None -> html.text("—")
  }
}

/// Record column for an audit event. Links via the live FK while the record
/// exists; once deleted (`record_id` NULL) the denormalized `record_key` is
/// shown unlinked so the row stays correlatable. The record type name is shown
/// as a muted suffix when available (NULL for system / deleted-record events).
fn event_record_cell(ev: RecordEvent) -> Element(msg) {
  case ev.record_id, ev.record_key {
    Some(id), _ ->
      html.span([], [record_link(id), record_type_suffix(ev.record_type_name)])
    None, Some(key) ->
      html.span([attribute.class("text-muted")], [
        html.text("#" <> int.to_string(key)),
      ])
    None, None -> html.text("—")
  }
}

/// Muted " TypeName" appended after a live record link; empty when absent
/// (system events and deleted records carry no record type to show).
fn record_type_suffix(name: Option(String)) -> Element(msg) {
  case name {
    Some(n) -> html.span([attribute.class("text-muted")], [html.text(" " <> n)])
    None -> element.none()
  }
}

fn record_link(id: Int) -> Element(msg) {
  html.a(
    [
      attribute.href(
        router.route_to_path(router.RecordDetail(int.to_string(id))),
      ),
    ],
    [html.text("#" <> int.to_string(id))],
  )
}

/// Patient column; links to the patient page when an id is present.
fn patient_cell(patient_id: Option(String)) -> Element(msg) {
  case patient_id {
    Some(pid) ->
      html.a([attribute.href(router.route_to_path(router.PatientDetail(pid)))], [
        html.text(pid),
      ])
    None -> html.text("—")
  }
}

// --- Column visibility (the feed component is shared across sources) ---

/// Per-record feeds drop the Record column (every row is the same record); the
/// global (cross-patient) and per-patient feeds keep it (records vary there).
fn show_record_col(source: Source) -> Bool {
  case source {
    RecordSource(_) -> False
    _ -> True
  }
}

/// Only the cross-patient global feed needs the Patient column — per-patient
/// and per-record feeds have a constant patient (the page subject).
fn show_patient_col(source: Source) -> Bool {
  case source {
    GlobalSource -> True
    _ -> False
  }
}

fn optional_cell(show: Bool, cell: Element(msg)) -> List(Element(msg)) {
  case show {
    True -> [cell]
    False -> []
  }
}

/// CSS class for a feed row — `activity-row-new` triggers the one-shot flash
/// animation on rows that just arrived via SSE live-update.
fn row_class(is_new: Bool) -> String {
  case is_new {
    True -> "activity-row-new"
    False -> ""
  }
}

// --- Record events table ---

pub fn events_table(
  events: List(RecordEvent),
  source: Source,
  new_ids: Set(Int),
  t: fn(Key) -> String,
) -> Element(msg) {
  let show_record = show_record_col(source)
  let show_patient = show_patient_col(source)
  case events {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(t(i18n.ActivityNoEvents)),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr(
              [],
              list.flatten([
                [html.th([], [html.text(t(i18n.ThDate))])],
                optional_cell(
                  show_record,
                  html.th([], [html.text(t(i18n.ThRecord))]),
                ),
                optional_cell(
                  show_patient,
                  html.th([], [html.text(t(i18n.ThPatient))]),
                ),
                [
                  html.th([], [html.text(t(i18n.ThEvent))]),
                  html.th([], [html.text(t(i18n.ThUser))]),
                  html.th([], [html.text(t(i18n.ThChange))]),
                  html.th([], [html.text(t(i18n.ThReason))]),
                ],
              ]),
            ),
          ]),
          keyed.tbody(
            [],
            list.map(events, fn(ev) {
              #(
                int.to_string(ev.id),
                event_row(
                  ev,
                  show_record,
                  show_patient,
                  set.contains(new_ids, ev.id),
                  t,
                ),
              )
            }),
          ),
        ]),
      ])
  }
}

fn event_row(
  ev: RecordEvent,
  show_record: Bool,
  show_patient: Bool,
  is_new: Bool,
  t: fn(Key) -> String,
) -> Element(msg) {
  html.tr(
    [attribute.class(row_class(is_new))],
    list.flatten([
      [html.td([], [html.text(datetime.format(ev.occurred_at))])],
      optional_cell(show_record, html.td([], [event_record_cell(ev)])),
      optional_cell(show_patient, html.td([], [patient_cell(ev.patient_id)])),
      [
        html.td([], [kind_badge(ev.kind, t)]),
        html.td([], [
          html.text(option.unwrap(ev.actor_name, t(i18n.ActivitySystemActor))),
        ]),
        html.td([], [status_transition(ev)]),
        html.td([attribute.class("text-muted")], [
          html.text(option.unwrap(ev.reason, "—")),
        ]),
      ],
    ]),
  )
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

pub fn runs_table(
  runs: List(PipelineRun),
  source: Source,
  new_ids: Set(String),
  t: fn(Key) -> String,
) -> Element(msg) {
  let show_record = show_record_col(source)
  let show_patient = show_patient_col(source)
  case runs {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text(t(i18n.ActivityNoRuns)),
      ])
    _ ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr(
              [],
              list.flatten([
                [html.th([], [html.text(t(i18n.ThTask))])],
                optional_cell(
                  show_record,
                  html.th([], [html.text(t(i18n.ThRecord))]),
                ),
                optional_cell(
                  show_patient,
                  html.th([], [html.text(t(i18n.ThPatient))]),
                ),
                [
                  html.th([], [html.text(t(i18n.ThStatus))]),
                  html.th([], [html.text(t(i18n.ThStarted))]),
                  html.th([], [html.text(t(i18n.ThDuration))]),
                  html.th([], [html.text(t(i18n.ThError))]),
                ],
              ]),
            ),
          ]),
          keyed.tbody(
            [],
            list.map(runs, fn(r) {
              #(
                r.id,
                run_row(
                  r,
                  show_record,
                  show_patient,
                  set.contains(new_ids, r.id),
                  t,
                ),
              )
            }),
          ),
        ]),
      ])
  }
}

fn run_row(
  run: PipelineRun,
  show_record: Bool,
  show_patient: Bool,
  is_new: Bool,
  t: fn(Key) -> String,
) -> Element(msg) {
  html.tr(
    [attribute.class(row_class(is_new))],
    list.flatten([
      [html.td([], [html.text(run.task_name)])],
      optional_cell(show_record, html.td([], [run_record_cell(run.record_id)])),
      optional_cell(show_patient, html.td([], [patient_cell(run.patient_id)])),
      [
        html.td([], [run_status_badge(run.status, t)]),
        html.td([], [html.text(datetime.format(run.started_at))]),
        html.td([], [html.text(duration_text(run.execution_time))]),
        html.td([attribute.class("text-muted")], [html.text(error_text(run))]),
      ],
    ]),
  )
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
