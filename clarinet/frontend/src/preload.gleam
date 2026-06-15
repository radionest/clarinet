// Preload module — self-contained MVU for OHIF study preloading.
//
// The viewer window is opened as a loading stub synchronously on Start (while
// the click's transient user activation is still valid — opening later would
// be popup-blocked), then navigated to the real viewer URL once preload
// reports "ready".
import api/dicomweb
import api/types
import config
import gleam/dynamic
import gleam/dynamic/decode
import gleam/int
import gleam/javascript/promise
import gleam/option.{type Option, None, Some}
import gleam/string
import gleam/time/timestamp
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import plinth/javascript/global
import utils/logger
import utils/viewer_window

// --- Model ---

pub type Model {
  Model(
    timer: Option(global.TimerID),
    progress: Option(ProgressState),
    window: Option(viewer_window.ViewerWindow),
    // Wall-clock ms of the last SSE progress push; PollTick skips the HTTP
    // poll while a push is fresher than the poll interval.
    last_push_ms: Int,
  )
}

pub type ProgressState {
  ProgressState(
    viewer_url: String,
    task_id: String,
    received: Int,
    total: Option(Int),
    status: String,
    study_index: Option(Int),
    study_count: Option(Int),
    study_received: Option(Int),
    study_total: Option(Int),
  )
}

// --- Msg ---

pub type Msg {
  Start(viewer_url: String, study_uids: List(String))
  WindowOpened(Result(viewer_window.ViewerWindow, Nil))
  Started(viewer_url: String, task_id: String)
  SetTimer(global.TimerID)
  PollTick(task_id: String)
  ProgressUpdate(
    task_id: String,
    result: Result(dynamic.Dynamic, types.ApiError),
  )
  /// SSE push carrying the same payload the poller would fetch.
  ProgressPush(task_id: String, payload: dynamic.Dynamic)
  Cancel
}

// --- OutMsg ---

pub type OutMsg {
  ShowError(String)
}

// --- Init ---

pub fn init() -> Model {
  Model(timer: None, progress: None, window: None, last_push_ms: 0)
}

// --- Queries ---

pub fn is_active(model: Model) -> Bool {
  option.is_some(model.progress)
}

fn loading_url() -> String {
  config.base_path() <> "/viewer_loading.html"
}

fn initial_progress(viewer_url: String, task_id: String) -> ProgressState {
  ProgressState(
    viewer_url: viewer_url,
    task_id: task_id,
    received: 0,
    total: None,
    status: "starting",
    study_index: None,
    study_count: None,
    study_received: None,
    study_total: None,
  )
}

// --- Update ---

pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg, model.progress {
    // Entry points — always handle regardless of progress state
    Start(viewer_url, study_uids), _ -> {
      let cleanup = effect.batch([stop_timer(model), close_window(model)])
      let open_eff = {
        use dispatch <- effect.from
        dispatch(WindowOpened(viewer_window.open(loading_url())))
      }
      let preload_eff = {
        use dispatch <- effect.from
        dicomweb.preload_studies(study_uids)
        |> promise.tap(fn(result) {
          dispatch(
            Started(viewer_url, case result {
              Ok(data) ->
                case decode.run(data, decode.at(["task_id"], decode.string)) {
                  Ok(tid) -> tid
                  Error(_) -> ""
                }
              Error(_) -> ""
            }),
          )
        })
        Nil
      }
      #(
        Model(
          timer: None,
          progress: Some(initial_progress(viewer_url, "")),
          window: None,
          last_push_ms: 0,
        ),
        effect.batch([cleanup, open_eff, preload_eff]),
        [],
      )
    }

    Cancel, _ -> {
      let cleanup = effect.batch([stop_timer(model), close_window(model)])
      #(init(), cleanup, [])
    }

    // Popup blocked: the user forbids pop-ups for the site — there is
    // nowhere to navigate, so don't even start polling.
    WindowOpened(Error(_)), _ -> {
      let cleanup = stop_timer(model)
      #(init(), cleanup, [
        ShowError(
          "Browser blocked the viewer window — allow pop-ups for this site",
        ),
      ])
    }

    // Window opened after the preload was already cancelled — close it
    WindowOpened(Ok(win)), None -> {
      #(model, effect.from(fn(_dispatch) { viewer_window.close(win) }), [])
    }

    WindowOpened(Ok(win)), Some(_) -> {
      #(Model(..model, window: Some(win)), effect.none(), [])
    }

    // No active preload — drop stale messages
    _, None -> #(model, effect.none(), [])

    // Active preload handlers
    Started(viewer_url, task_id), Some(_) -> {
      case task_id {
        "" -> {
          // POST failed — degrade: open the viewer without preload
          let nav_eff = navigate_window(model, viewer_url)
          #(init(), nav_eff, [])
        }
        _ -> {
          let timer_effect = {
            use dispatch <- effect.from
            let timer_id =
              global.set_interval(2000, fn() { dispatch(PollTick(task_id)) })
            dispatch(PollTick(task_id))
            dispatch(SetTimer(timer_id))
          }
          #(
            Model(
              ..model,
              progress: Some(initial_progress(viewer_url, task_id)),
            ),
            timer_effect,
            [],
          )
        }
      }
    }

    SetTimer(timer_id), Some(_) -> {
      #(Model(..model, timer: Some(timer_id)), effect.none(), [])
    }

    PollTick(task_id), Some(progress) -> {
      case progress.task_id == task_id {
        False -> #(model, effect.none(), [])
        True ->
          // A fresh SSE push means the stream is live — skip the HTTP poll.
          // When the stream drops, pushes stop and polling resumes.
          case now_ms() - model.last_push_ms < 2500 {
            True -> #(model, effect.none(), [])
            False -> {
              let poll_effect = {
                use dispatch <- effect.from
                dicomweb.preload_progress(task_id)
                |> promise.tap(fn(result) {
                  dispatch(ProgressUpdate(task_id, result))
                })
                Nil
              }
              #(model, poll_effect, [])
            }
          }
      }
    }

    ProgressUpdate(task_id, Ok(data)), Some(progress) ->
      case progress.task_id == task_id {
        False -> #(model, effect.none(), [])
        True -> apply_progress(model, progress, task_id, data)
      }

    // SSE push for the active task — same handling as a poll result, plus a
    // last_push_ms stamp so PollTick can back off while the stream is alive.
    ProgressPush(task_id, payload), Some(progress) ->
      case progress.task_id == task_id {
        False -> #(model, effect.none(), [])
        True ->
          apply_progress(
            Model(..model, last_push_ms: now_ms()),
            progress,
            task_id,
            payload,
          )
      }

    ProgressUpdate(_, Error(err)), Some(_) -> {
      case err {
        types.AuthError(_) -> {
          logger.warn("preload", "Session expired during preload")
          let cleanup = effect.batch([stop_timer(model), close_window(model)])
          #(init(), cleanup, [ShowError("Session expired")])
        }
        _ -> {
          logger.warn(
            "preload",
            "Progress poll failed: " <> string.inspect(err),
          )
          #(model, effect.none(), [])
        }
      }
    }
  }
}

// --- Helpers ---

/// Apply a progress payload (from a poll result or an SSE push) to the active
/// preload. Shared by ProgressUpdate and ProgressPush.
fn apply_progress(
  model: Model,
  progress: ProgressState,
  task_id: String,
  data: dynamic.Dynamic,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let status =
    decode.run(data, decode.at(["status"], decode.string))
    |> option.from_result
    |> option.unwrap("unknown")

  case status {
    "ready" -> {
      // A closed stub means the user changed their mind — silent cancel
      let stop_eff = stop_timer(model)
      let nav_eff = navigate_window(model, progress.viewer_url)
      #(init(), effect.batch([stop_eff, nav_eff]), [])
    }
    "error" -> {
      let cleanup = effect.batch([stop_timer(model), close_window(model)])
      let error_msg =
        decode.run(data, decode.at(["error"], decode.string))
        |> option.from_result
        |> option.unwrap("Preload failed")
      #(init(), cleanup, [ShowError(error_msg)])
    }
    // Progress entry fell out of the server-side TTL cache
    "not_found" -> {
      let cleanup = effect.batch([stop_timer(model), close_window(model)])
      #(init(), cleanup, [
        ShowError("Preload status expired — please try again"),
      ])
    }
    _ -> {
      let int_field = fn(key: String) {
        decode.run(data, decode.at([key], decode.int))
        |> option.from_result
      }
      let new_progress =
        ProgressState(
          viewer_url: progress.viewer_url,
          task_id: task_id,
          received: int_field("received") |> option.unwrap(0),
          total: int_field("total"),
          status: status,
          study_index: int_field("study_index"),
          study_count: int_field("study_count"),
          study_received: int_field("study_received"),
          study_total: int_field("study_total"),
        )
      #(Model(..model, progress: Some(new_progress)), effect.none(), [])
    }
  }
}

fn now_ms() -> Int {
  let #(seconds, nanoseconds) =
    timestamp.system_time()
    |> timestamp.to_unix_seconds_and_nanoseconds()
  seconds * 1000 + nanoseconds / 1_000_000
}

pub fn stop_timer(model: Model) -> Effect(Msg) {
  case model.timer {
    Some(timer_id) ->
      effect.from(fn(_dispatch) { global.clear_interval(timer_id) })
    None -> effect.none()
  }
}

pub fn close_window(model: Model) -> Effect(Msg) {
  case model.window {
    Some(win) -> effect.from(fn(_dispatch) { viewer_window.close(win) })
    None -> effect.none()
  }
}

/// Full cleanup for route change / logout: stop polling and close the stub.
pub fn cleanup(model: Model) -> Effect(Msg) {
  effect.batch([stop_timer(model), close_window(model)])
}

fn navigate_window(model: Model, viewer_url: String) -> Effect(Msg) {
  case model.window {
    Some(win) ->
      effect.from(fn(_dispatch) {
        case viewer_window.is_closed(win) {
          // User closed the stub during preload — treat as silent cancel
          True -> Nil
          False -> viewer_window.navigate(win, viewer_url)
        }
      })
    None -> effect.none()
  }
}

// --- View ---

pub fn view_modal(state: ProgressState) -> Element(Msg) {
  let progress_text = case state.status {
    "starting" -> "Starting preload..."
    "fetching" ->
      case state.study_received, state.study_total {
        Some(r), Some(t) ->
          "Received " <> int.to_string(r) <> " of ~" <> int.to_string(t)
        Some(r), None -> "Received " <> int.to_string(r) <> " images..."
        None, _ -> "Received " <> int.to_string(state.received) <> " images..."
      }
    "ready" -> "Ready!"
    _ -> "Loading..."
  }

  let study_counter = case state.study_index, state.study_count {
    Some(i), Some(n) if n > 1 ->
      html.p([attribute.class("preload-study-counter")], [
        html.text("Study " <> int.to_string(i) <> " of " <> int.to_string(n)),
      ])
    _, _ -> element.none()
  }

  let progress_bar = case state.study_total {
    Some(t) if t > 0 -> {
      let received = option.unwrap(state.study_received, 0)
      let pct = { received * 100 } / t
      let width = int.to_string(int.min(pct, 100)) <> "%"
      html.div([attribute.class("progress-bar-container")], [
        html.div(
          [
            attribute.class("progress-bar"),
            attribute.style("width", width),
          ],
          [],
        ),
      ])
    }
    _ ->
      html.div([attribute.class("progress-bar-container")], [
        html.div(
          [attribute.class("progress-bar progress-bar-indeterminate")],
          [],
        ),
      ])
  }

  html.div([attribute.class("modal-backdrop")], [
    html.div([attribute.class("modal")], [
      html.div([attribute.class("modal-header")], [
        html.h3([attribute.class("modal-title")], [
          html.text("Loading images..."),
        ]),
      ]),
      study_counter,
      html.p([], [html.text(progress_text)]),
      progress_bar,
      html.div([attribute.class("modal-footer")], [
        html.button(
          [
            attribute.class("btn btn-secondary"),
            event.on_click(Cancel),
          ],
          [html.text("Cancel")],
        ),
      ]),
    ]),
  ])
}
