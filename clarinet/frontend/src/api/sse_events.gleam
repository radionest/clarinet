//// Decoder for the SSE wire format (server -> client). The JSON payloads match
//// the backend `EntityEvent` / `TaskProgressEvent` `to_wire()` output.

import gleam/dynamic
import gleam/dynamic/decode
import gleam/json
import gleam/option.{type Option, None}

pub type Action {
  Created
  Updated
  Deleted
}

pub type EntityEvent {
  EntityEvent(
    entity: String,
    action: Action,
    id: String,
    record_type_name: Option(String),
    user_id: Option(String),
  )
}

pub type SseEvent {
  Entity(EntityEvent)
  TaskProgress(task: String, task_id: String, payload: dynamic.Dynamic)
  AuthExpired
  Ping
}

fn action_decoder() -> decode.Decoder(Action) {
  use raw <- decode.then(decode.string)
  case raw {
    "created" -> decode.success(Created)
    "updated" -> decode.success(Updated)
    "deleted" -> decode.success(Deleted)
    _ -> decode.failure(Created, "Action")
  }
}

fn entity_decoder() -> decode.Decoder(SseEvent) {
  use entity <- decode.field("entity", decode.string)
  use action <- decode.field("action", action_decoder())
  use id <- decode.field("id", decode.string)
  use record_type_name <- decode.optional_field(
    "record_type_name",
    None,
    decode.optional(decode.string),
  )
  use user_id <- decode.optional_field(
    "user_id",
    None,
    decode.optional(decode.string),
  )
  decode.success(
    Entity(EntityEvent(entity:, action:, id:, record_type_name:, user_id:)),
  )
}

fn task_progress_decoder() -> decode.Decoder(SseEvent) {
  use task <- decode.field("task", decode.string)
  use task_id <- decode.field("task_id", decode.string)
  use payload <- decode.field("payload", decode.dynamic)
  decode.success(TaskProgress(task:, task_id:, payload:))
}

fn frame_decoder() -> decode.Decoder(SseEvent) {
  use type_ <- decode.field("type", decode.string)
  case type_ {
    "entity" -> entity_decoder()
    "task_progress" -> task_progress_decoder()
    "auth_expired" -> decode.success(AuthExpired)
    "ping" -> decode.success(Ping)
    _ -> decode.failure(Ping, "SseEvent")
  }
}

pub fn decode_frame(text: String) -> Result(SseEvent, Nil) {
  case json.parse(text, frame_decoder()) {
    Ok(event) -> Ok(event)
    Error(_) -> Error(Nil)
  }
}
