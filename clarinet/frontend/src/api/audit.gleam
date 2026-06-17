// Audit API endpoints: record events + pipeline task runs.
import api/http_client
import api/models.{type PipelineRun, type RecordEvent}
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/string
import gleam/uri

// --- Decoders ---

pub fn record_event_decoder() -> decode.Decoder(RecordEvent) {
  use id <- decode.field("id", decode.int)
  use record_id <- decode.optional_field(
    "record_id",
    None,
    decode.optional(decode.int),
  )
  use record_key <- decode.optional_field(
    "record_key",
    None,
    decode.optional(decode.int),
  )
  use record_type_name <- decode.optional_field(
    "record_type_name",
    None,
    decode.optional(decode.string),
  )
  use patient_id <- decode.optional_field(
    "patient_id",
    None,
    decode.optional(decode.string),
  )
  use kind <- decode.field("kind", decode.string)
  use actor_name <- decode.optional_field(
    "actor_name",
    None,
    decode.optional(decode.string),
  )
  use from_status <- decode.optional_field(
    "from_status",
    None,
    decode.optional(decode.string),
  )
  use to_status <- decode.optional_field(
    "to_status",
    None,
    decode.optional(decode.string),
  )
  use reason <- decode.optional_field(
    "reason",
    None,
    decode.optional(decode.string),
  )
  use occurred_at <- decode.field("occurred_at", decode.string)
  decode.success(models.RecordEvent(
    id: id,
    record_id: record_id,
    record_key: record_key,
    record_type_name: record_type_name,
    patient_id: patient_id,
    kind: kind,
    actor_name: actor_name,
    from_status: from_status,
    to_status: to_status,
    reason: reason,
    occurred_at: occurred_at,
  ))
}

pub fn pipeline_run_decoder() -> decode.Decoder(PipelineRun) {
  use id <- decode.field("id", decode.string)
  use task_name <- decode.field("task_name", decode.string)
  use queue <- decode.field("queue", decode.string)
  use status <- decode.field("status", decode.string)
  use record_id <- decode.optional_field(
    "record_id",
    None,
    decode.optional(decode.int),
  )
  use patient_id <- decode.optional_field(
    "patient_id",
    None,
    decode.optional(decode.string),
  )
  use started_at <- decode.field("started_at", decode.string)
  use finished_at <- decode.optional_field(
    "finished_at",
    None,
    decode.optional(decode.string),
  )
  use execution_time <- decode.optional_field(
    "execution_time",
    None,
    decode.optional(decode.float),
  )
  use retry_count <- decode.optional_field("retry_count", 0, decode.int)
  use error_type <- decode.optional_field(
    "error_type",
    None,
    decode.optional(decode.string),
  )
  use error_message <- decode.optional_field(
    "error_message",
    None,
    decode.optional(decode.string),
  )
  decode.success(models.PipelineRun(
    id: id,
    task_name: task_name,
    queue: queue,
    status: status,
    record_id: record_id,
    patient_id: patient_id,
    started_at: started_at,
    finished_at: finished_at,
    execution_time: execution_time,
    retry_count: retry_count,
    error_type: error_type,
    error_message: error_message,
  ))
}

// --- Per-record (any user with record access) ---

pub fn get_record_events(
  record_id: String,
) -> Promise(Result(List(RecordEvent), ApiError)) {
  http_client.get("/records/" <> record_id <> "/events")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(record_event_decoder()),
      "Invalid events data",
    ))
  })
}

pub fn get_record_runs(
  record_id: String,
) -> Promise(Result(List(PipelineRun), ApiError)) {
  http_client.get("/records/" <> record_id <> "/runs")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(pipeline_run_decoder()),
      "Invalid runs data",
    ))
  })
}

// --- Global feed / per-patient (admin) ---
// `patient_id = None` → server-wide feed; `Some(id)` → scoped to a patient.
// The remaining optional params are the global feed's UI filters; per-patient
// callers pass them as `None`.

pub fn list_events(
  patient_id: Option(String),
  kind: Option(String),
  actor_id: Option(String),
  record_type_name: Option(String),
  since: Option(String),
) -> Promise(Result(List(RecordEvent), ApiError)) {
  let query =
    build_query([
      #("patient_id", patient_id),
      #("kind", kind),
      #("actor_id", actor_id),
      #("record_type_name", record_type_name),
      #("since", since),
    ])
  http_client.get("/admin/records/events" <> query)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(record_event_decoder()),
      "Invalid events data",
    ))
  })
}

pub fn list_runs(
  patient_id: Option(String),
  status: Option(String),
  task_name: Option(String),
  since: Option(String),
) -> Promise(Result(List(PipelineRun), ApiError)) {
  let query =
    build_query([
      #("patient_id", patient_id),
      #("status", status),
      #("task_name", task_name),
      #("since", since),
    ])
  http_client.get("/pipelines/runs" <> query)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(pipeline_run_decoder()),
      "Invalid runs data",
    ))
  })
}

/// Build a `?k=v&...` query string from optional params, dropping the ones set
/// to `None` and percent-encoding each value. Returns "" when nothing is set.
fn build_query(params: List(#(String, Option(String)))) -> String {
  let pairs =
    list.filter_map(params, fn(param) {
      let #(key, value) = param
      case value {
        Some(v) -> Ok(key <> "=" <> uri.percent_encode(v))
        None -> Error(Nil)
      }
    })
  case pairs {
    [] -> ""
    _ -> "?" <> string.join(pairs, "&")
  }
}
