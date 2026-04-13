import api/types
import gleam/dynamic
import gleam/dynamic/decode
import gleam/json
import gleam/option.{None, Some}
import gleeunit/should
import preload.{type OutMsg, Model, OpenViewer, ProgressState, ShowError}

fn inactive_model() -> preload.Model {
  preload.init()
}

fn active_model() -> preload.Model {
  Model(
    timer: None,
    progress: Some(ProgressState(
      viewer_url: "http://viewer/ohif",
      task_id: "task-1",
      study_uid: "1.2.3",
      received: 5,
      total: Some(10),
      status: "fetching",
    )),
  )
}

fn ready_data() -> dynamic.Dynamic {
  let assert Ok(data) =
    json.parse("{\"status\":\"ready\",\"received\":10,\"total\":10}", decode.dynamic)
  data
}

fn fetching_data() -> dynamic.Dynamic {
  let assert Ok(data) =
    json.parse("{\"status\":\"fetching\",\"received\":7,\"total\":10}", decode.dynamic)
  data
}

fn error_data() -> dynamic.Dynamic {
  let assert Ok(data) =
    json.parse(
      "{\"status\":\"error\",\"error\":\"PACS unreachable\"}",
      decode.dynamic,
    )
  data
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
  |> preload.update(preload.PollTick("task-1", "http://viewer/ohif", "1.2.3"))
  |> assert_noop(inactive_model())
}

pub fn progress_ready_inactive_test() {
  inactive_model()
  |> preload.update(preload.ProgressUpdate(
    "task-1",
    "http://viewer/ohif",
    "1.2.3",
    Ok(ready_data()),
  ))
  |> assert_noop(inactive_model())
}

pub fn progress_error_inactive_test() {
  inactive_model()
  |> preload.update(preload.ProgressUpdate(
    "task-1",
    "http://viewer/ohif",
    "1.2.3",
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
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate(
      "task-1",
      "http://viewer/ohif",
      "1.2.3",
      Ok(ready_data()),
    ))
  should.equal(model.progress, None)
  should.equal(model.timer, None)
  should.equal(out_msgs, [OpenViewer("http://viewer/ohif")])
}

pub fn progress_error_status_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate(
      "task-1",
      "http://viewer/ohif",
      "1.2.3",
      Ok(error_data()),
    ))
  should.equal(model.progress, None)
  should.equal(out_msgs, [ShowError("PACS unreachable")])
}

pub fn progress_fetching_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate(
      "task-1",
      "http://viewer/ohif",
      "1.2.3",
      Ok(fetching_data()),
    ))
  let assert Some(progress) = model.progress
  should.equal(progress.received, 7)
  should.equal(progress.status, "fetching")
  should.equal(out_msgs, [])
}

pub fn progress_auth_error_active_test() {
  let #(model, _, out_msgs) =
    active_model()
    |> preload.update(preload.ProgressUpdate(
      "task-1",
      "http://viewer/ohif",
      "1.2.3",
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
      "http://viewer/ohif",
      "1.2.3",
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
