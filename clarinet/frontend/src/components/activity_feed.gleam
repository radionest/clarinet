// Reusable audit/activity views: tab bar + record-event table + pipeline-run
// table. State (active tab, LoadStatus, data) lives in the host page; this
// module is a pure projection parameterised by the page's Msg via callbacks.
import api/models.{type PipelineRun, type RecordEvent}
import clarinet_frontend/i18n.{type Key}
import gleam/float
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import utils/datetime
import utils/load_status.{type LoadStatus}

pub type ActivityTab {
  EventsTab
  RunsTab
}

/// Full activity body: tab bar + the active tab's table wrapped in the
/// tri-state LoadStatus renderer. `on_select` / `on_retry` lift tab and retry
/// clicks into the host page's own Msg.
pub fn view(
  active: ActivityTab,
  events_status: LoadStatus,
  events: List(RecordEvent),
  runs_status: LoadStatus,
  runs: List(PipelineRun),
  on_select: fn(ActivityTab) -> msg,
  on_retry: fn(ActivityTab) -> msg,
  t: fn(Key) -> String,
) -> Element(msg) {
  html.div([attribute.class("activity")], [
    tabs(active, on_select, t),
    case active {
      EventsTab ->
        load_status.render(
          events_status,
          fn() { loading_view(t) },
          fn() { events_table(events, t) },
          fn(m) { error_view(m, on_retry(EventsTab), t) },
        )
      RunsTab ->
        load_status.render(
          runs_status,
          fn() { loading_view(t) },
          fn() { runs_table(runs, t) },
          fn(m) { error_view(m, on_retry(RunsTab), t) },
        )
    },
  ])
}

fn tabs(
  active: ActivityTab,
  on_select: fn(ActivityTab) -> msg,
  t: fn(Key) -> String,
) -> Element(msg) {
  html.div([attribute.class("activity-tabs")], [
    tab_button(i18n.ActivityTabEvents, EventsTab, active, on_select, t),
    tab_button(i18n.ActivityTabRuns, RunsTab, active, on_select, t),
  ])
}

fn tab_button(
  label: Key,
  tab: ActivityTab,
  active: ActivityTab,
  on_select: fn(ActivityTab) -> msg,
  t: fn(Key) -> String,
) -> Element(msg) {
  let class = case tab == active {
    True -> "btn btn-primary"
    False -> "btn btn-outline"
  }
  html.button([attribute.class(class), event.on_click(on_select(tab))], [
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
  retry_msg: msg,
  t: fn(Key) -> String,
) -> Element(msg) {
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
