// Preload module — self-contained MVU for OHIF study preloading
import api/dicomweb
import api/types
import gleam/dynamic
import gleam/dynamic/decode
import gleam/int
import gleam/javascript/promise
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import plinth/javascript/global
import utils/logger

// --- Model ---

pub type Model {
  Model(timer: Option(global.TimerID), progress: Option(ProgressState))
}

pub type ProgressState {
  ProgressState(
    viewer_url: String,
    task_id: String,
    study_uid: String,
    received: Int,
    total: Option(Int),
    status: String,
  )
}

// --- Msg ---

pub type Msg {
  Start(viewer_url: String, study_uid: String)
  Started(viewer_url: String, task_id: String, study_uid: String)
  SetTimer(global.TimerID)
  PollTick(task_id: String, viewer_url: String, study_uid: String)
  ProgressUpdate(
    task_id: String,
    viewer_url: String,
    study_uid: String,
    result: Result(dynamic.Dynamic, types.ApiError),
  )
  Cancel
}

// --- OutMsg ---

pub type OutMsg {
  OpenViewer(url: String)
  ShowError(String)
}

// --- Init ---

pub fn init() -> Model {
  Model(timer: None, progress: None)
}

// --- Queries ---

pub fn is_active(model: Model) -> Bool {
  option.is_some(model.progress)
}

// --- Update ---

pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg, model.progress {
    // Entry points — always handle regardless of progress state
    Start(viewer_url, study_uid), _ -> {
      let eff = {
        use dispatch <- effect.from
        dicomweb.preload_study(study_uid)
        |> promise.tap(fn(result) {
          dispatch(Started(viewer_url, case result {
            Ok(data) ->
              case decode.run(data, decode.at(["task_id"], decode.string)) {
                Ok(tid) -> tid
                Error(_) -> ""
              }
            Error(_) -> ""
          }, study_uid))
        })
        Nil
      }
      let progress =
        ProgressState(
          viewer_url: viewer_url,
          task_id: "",
          study_uid: study_uid,
          received: 0,
          total: None,
          status: "starting",
        )
      #(Model(..model, progress: Some(progress)), eff, [])
    }

    Cancel, _ -> {
      let stop_eff = stop_timer(model)
      #(Model(timer: None, progress: None), stop_eff, [])
    }

    // No active preload — drop stale messages
    _, None -> #(model, effect.none(), [])

    // Active preload handlers
    Started(viewer_url, task_id, study_uid), Some(_) -> {
      case task_id {
        "" ->
          // Failed to start — just open viewer directly
          #(Model(..model, progress: None), effect.none(), [OpenViewer(viewer_url)])
        _ -> {
          let timer_effect = {
            use dispatch <- effect.from
            let timer_id =
              global.set_interval(2000, fn() {
                dispatch(PollTick(task_id, viewer_url, study_uid))
              })
            dispatch(PollTick(task_id, viewer_url, study_uid))
            dispatch(SetTimer(timer_id))
          }
          let progress =
            ProgressState(
              viewer_url: viewer_url,
              task_id: task_id,
              study_uid: study_uid,
              received: 0,
              total: None,
              status: "starting",
            )
          #(Model(..model, progress: Some(progress)), timer_effect, [])
        }
      }
    }

    SetTimer(timer_id), Some(_) -> {
      #(Model(..model, timer: Some(timer_id)), effect.none(), [])
    }

    PollTick(task_id, viewer_url, study_uid), Some(_) -> {
      let poll_effect = {
        use dispatch <- effect.from
        dicomweb.preload_progress(study_uid, task_id)
        |> promise.tap(fn(result) {
          dispatch(ProgressUpdate(task_id, viewer_url, study_uid, result))
        })
        Nil
      }
      #(model, poll_effect, [])
    }

    ProgressUpdate(task_id, viewer_url, study_uid, Ok(data)), Some(_) -> {
      let status =
        decode.run(data, decode.at(["status"], decode.string))
        |> option.from_result
        |> option.unwrap("unknown")
      let received =
        decode.run(data, decode.at(["received"], decode.int))
        |> option.from_result
        |> option.unwrap(0)
      let total =
        decode.run(data, decode.at(["total"], decode.int))
        |> option.from_result

      case status {
        "ready" -> {
          let stop_eff = stop_timer(model)
          #(
            Model(timer: None, progress: None),
            stop_eff,
            [OpenViewer(viewer_url)],
          )
        }
        "error" -> {
          let stop_eff = stop_timer(model)
          let error_msg =
            decode.run(data, decode.at(["error"], decode.string))
            |> option.from_result
            |> option.unwrap("Preload failed")
          #(Model(timer: None, progress: None), stop_eff, [ShowError(error_msg)])
        }
        _ -> {
          let progress =
            ProgressState(
              viewer_url: viewer_url,
              task_id: task_id,
              study_uid: study_uid,
              received: received,
              total: total,
              status: status,
            )
          #(Model(..model, progress: Some(progress)), effect.none(), [])
        }
      }
    }

    ProgressUpdate(_, _, _, Error(err)), Some(_) -> {
      case err {
        types.AuthError(_) -> {
          let stop_eff = stop_timer(model)
          #(Model(timer: None, progress: None), stop_eff, [
            ShowError("Session expired"),
          ])
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

pub fn stop_timer(model: Model) -> Effect(Msg) {
  case model.timer {
    Some(timer_id) ->
      effect.from(fn(_dispatch) { global.clear_interval(timer_id) })
    None -> effect.none()
  }
}

// --- View ---

pub fn view_modal(state: ProgressState) -> Element(Msg) {
  let progress_text = case state.status {
    "checking_cache" -> "Checking cache..."
    "starting" -> "Starting preload..."
    "fetching" ->
      case state.total {
        Some(t) ->
          "Received "
          <> int.to_string(state.received)
          <> " of ~"
          <> int.to_string(t)
        None ->
          "Received " <> int.to_string(state.received) <> " images..."
      }
    "ready" -> "Ready!"
    _ -> "Loading..."
  }

  let progress_bar = case state.total {
    Some(t) if t > 0 -> {
      let pct = { state.received * 100 } / t
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
