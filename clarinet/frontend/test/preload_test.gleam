import api/types
import gleam/dynamic
import gleam/dynamic/decode
import gleam/json
import gleam/option.{None, Some}
import gleeunit/should
import preload.{type OutMsg, Model, ProgressState, ShowError}

// Window-handle branches (navigate/close on a live ViewerWindow) are only
// exercised through the None path here — a ViewerWindow value cannot be
// constructed outside a browser. Navigation itself is covered by e2e.

fn inactive_model() -> preload.Model {
  preload.init()
}

fn active_model() -> preload.Model {
  Model(timer: None, progress: Some(active_progress()), window: None)
}

fn active_progress() -> preload.ProgressState {
  ProgressState(
    viewer_url: "http://viewer/ohif",
    task_id: "task-1",
    received: 5,
    total: Some(10),
    status: "fetching",
    study_index: None,
    study_count: None,
    study_received: None,
    study_total: None,
  )
}

fn parse_data(raw: String) -> dynamic.Dynamic {
  let assert Ok(data) = json.parse(raw, decode.dynamic)
  data
}

fn ready_data() -> dynamic.Dynamic {
  parse_data("{\"status\":\"ready\",\"received\":10,\"total\":10}")
}

fn fetching_data() -> dynamic.Dynamic {
  parse_data(
    "{\"status\":\"fetching\",\"received\":7,\"total\":10,"
    <> "\"study_index\":2,\"study_count\":3,"
    <> "\"study_received\":4,\"study_total\":6}",
  )
}

fn error_data() -> dynamic.Dynamic {
  parse_data("{\"status\":\"error\",\"error\":\"PACS unreachable\"}")
}

fn not_found_data() -> dynamic.Dynamic {
  parse_data("{\"status\":\"not_found\"}")
}

fn assert_noop(
  result: #(preload.Model, a, List(OutMsg)),
  expected_model: preload.Model,
) {
  let #(model, _, out_msgs) = result
  should.equal(model.progress, expected_model.progress)
  should.equal(model.timer, expected_model.timer)
  should.equal(out_msgs, [])
}

// --- Inactive preload: stale messages are dropped ---

pub fn poll_tick_inactive_test() {
  inactive_model()
  |> preload.update(preload.PollTick("task-1"))
  |> assert_noop(inactive_model())
}

pub fn progress_ready_inactive_test() {
  inactive_model()
  |> preload.update(preload.ProgressUpdate("task-1", Ok(ready_data())))
  |> assert_noop(inactive_model())
}

pub fn progress_error_inactive_test() {
  inactive_model()
  |> preload.update(preload.ProgressUpdate(
    "task-1",
    Error(types.NetworkError("timeout")),
  ))
  |> assert_noop(inactive_model())
}

pub fn cancel_inactive_test() {
  let #(model, _, out_msgs) =
    inactive_model()
    |> preload.update(preload.Cancel)
  should.equal(model.timer, None)
  should.equal(model.progress, None)
  should.equal(out_msgs, [])
}

// --- Active preload: normal operation ---

pub fn progress_ready_active_test() {
  // Navigation moved into an effect on the window handle — from the unit's
  // point of view "ready" is observable only as a full state reset.
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate("task-1", Ok(ready_data())))
  should.equal(model.progress, None)
  should.equal(model.timer, None)
  should.equal(out_msgs, [])
}

pub fn progress_error_status_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate("task-1", Ok(error_data())))
  should.equal(model.progress, None)
  should.equal(out_msgs, [ShowError("PACS unreachable")])
}

pub fn progress_not_found_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate("task-1", Ok(not_found_data())))
  should.equal(model.progress, None)
  should.equal(model.timer, None)
  should.equal(out_msgs, [
    ShowError("Preload status expired — please try again"),
  ])
}

pub fn progress_fetching_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate("task-1", Ok(fetching_data())))
  let assert Some(progress) = model.progress
  should.equal(progress.received, 7)
  should.equal(progress.status, "fetching")
  should.equal(progress.study_index, Some(2))
  should.equal(progress.study_count, Some(3))
  should.equal(progress.study_received, Some(4))
  should.equal(progress.study_total, Some(6))
  should.equal(out_msgs, [])
}

pub fn progress_auth_error_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate(
      "task-1",
      Error(types.AuthError("Unauthorized")),
    ))
  should.equal(model.progress, None)
  should.equal(model.timer, None)
  should.equal(out_msgs, [ShowError("Session expired")])
}

pub fn progress_network_error_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate(
      "task-1",
      Error(types.NetworkError("timeout")),
    ))
  should.equal(model.progress, active_model().progress)
  should.equal(out_msgs, [])
}

pub fn cancel_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.Cancel)
  should.equal(model.progress, None)
  should.equal(model.timer, None)
  should.equal(out_msgs, [])
}

// --- Window handling ---

pub fn window_blocked_test() {
  // Popup blocked entirely: error toast + full reset, polling never starts
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.WindowOpened(Error(Nil)))
  should.equal(model.progress, None)
  should.equal(model.timer, None)
  should.equal(model.window, None)
  should.equal(out_msgs, [
    ShowError("Browser blocked the viewer window — allow pop-ups for this site"),
  ])
}

pub fn started_without_task_id_degrades_test() {
  // POST /preload failed: no polling, state reset (the stub window, when
  // present, is navigated straight to the viewer inside the effect)
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.Started("http://viewer/ohif", ""))
  should.equal(model.progress, None)
  should.equal(model.timer, None)
  should.equal(out_msgs, [])
}

pub fn started_with_task_id_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.Started("http://viewer/ohif", "task-2"))
  let assert Some(progress) = model.progress
  should.equal(progress.task_id, "task-2")
  should.equal(progress.status, "starting")
  should.equal(out_msgs, [])
}
