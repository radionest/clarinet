import api/sse_events.{
  AuthExpired, Created, Deleted, Entity, EntityEvent, Ping, Presence,
  TaskProgress,
}
import gleam/option.{None, Some}
import gleeunit/should

pub fn decode_record_with_user_test() {
  let frame =
    "{\"type\":\"entity\",\"entity\":\"record\",\"action\":\"created\","
    <> "\"id\":\"123\",\"record_type_name\":\"ct_seg\",\"user_id\":\"u-1\"}"
  frame
  |> sse_events.decode_frame
  |> should.equal(
    Ok(
      Entity(EntityEvent(
        entity: "record",
        action: Created,
        id: "123",
        record_type_name: Some("ct_seg"),
        user_id: Some("u-1"),
      )),
    ),
  )
}

pub fn decode_entity_without_optionals_test() {
  "{\"type\":\"entity\",\"entity\":\"patient\",\"action\":\"deleted\",\"id\":\"P1\"}"
  |> sse_events.decode_frame
  |> should.equal(
    Ok(
      Entity(EntityEvent(
        entity: "patient",
        action: Deleted,
        id: "P1",
        record_type_name: None,
        user_id: None,
      )),
    ),
  )
}

pub fn decode_task_progress_test() {
  let frame =
    "{\"type\":\"task_progress\",\"task\":\"preload\",\"task_id\":\"t1\","
    <> "\"payload\":{\"status\":\"fetching\"}}"
  case sse_events.decode_frame(frame) {
    Ok(TaskProgress(task: "preload", task_id: "t1", payload: _)) ->
      should.be_true(True)
    _ -> should.be_true(False)
  }
}

pub fn decode_auth_expired_test() {
  "{\"type\":\"auth_expired\"}"
  |> sse_events.decode_frame
  |> should.equal(Ok(AuthExpired))
}

pub fn decode_ping_test() {
  "{\"type\":\"ping\"}"
  |> sse_events.decode_frame
  |> should.equal(Ok(Ping))
}

pub fn decode_garbage_test() {
  "not json"
  |> sse_events.decode_frame
  |> should.equal(Error(Nil))
}

pub fn decode_unknown_type_test() {
  "{\"type\":\"wat\"}"
  |> sse_events.decode_frame
  |> should.equal(Error(Nil))
}

pub fn decode_bad_action_test() {
  "{\"type\":\"entity\",\"entity\":\"record\",\"action\":\"frobnicate\",\"id\":\"1\"}"
  |> sse_events.decode_frame
  |> should.equal(Error(Nil))
}

pub fn decode_presence_online_test() {
  "{\"type\":\"presence\",\"user_id\":\"u-1\",\"online\":true}"
  |> sse_events.decode_frame
  |> should.equal(Ok(Presence(user_id: "u-1", online: True)))
}

pub fn decode_presence_offline_test() {
  "{\"type\":\"presence\",\"user_id\":\"u-2\",\"online\":false}"
  |> sse_events.decode_frame
  |> should.equal(Ok(Presence(user_id: "u-2", online: False)))
}
