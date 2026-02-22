// Record API endpoints
import api/http_client
import api/models.{type Record}
import api/types.{type ApiError}
import gleam/dynamic
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/option.{None}
import gleam/result

// Get all records
pub fn get_records() -> Promise(Result(List(Record), ApiError)) {
  http_client.get("/records/")
  |> promise.map(fn(res) { result.try(res, decode_records) })
}

// Get current user's records
pub fn get_my_records() -> Promise(Result(List(Record), ApiError)) {
  http_client.get("/records/my")
  |> promise.map(fn(res) { result.try(res, decode_records) })
}

// Public decoder for reuse
pub fn record_decoder() -> decode.Decoder(Record) {
  use id <- decode.optional_field("id", None, decode.optional(decode.int))
  use context_info <- decode.optional_field(
    "context_info",
    None,
    decode.optional(decode.string),
  )
  use status_str <- decode.field("status", decode.string)
  use study_uid <- decode.optional_field(
    "study_uid",
    None,
    decode.optional(decode.string),
  )
  use series_uid <- decode.optional_field(
    "series_uid",
    None,
    decode.optional(decode.string),
  )
  use record_type_name <- decode.field("record_type_name", decode.string)
  use user_id <- decode.optional_field(
    "user_id",
    None,
    decode.optional(decode.string),
  )
  use patient_id <- decode.field("patient_id", decode.string)

  let status = parse_status(status_str)

  decode.success(models.Record(
    id: id,
    context_info: context_info,
    status: status,
    study_uid: study_uid,
    series_uid: series_uid,
    record_type_name: record_type_name,
    user_id: user_id,
    patient_id: patient_id,
    study_anon_uid: None,
    series_anon_uid: None,
    clarinet_storage_path: None,
    files: None,
    patient: None,
    study: None,
    series: None,
    record_type: None,
    user: None,
    data: None,
    created_at: None,
    changed_at: None,
    started_at: None,
    finished_at: None,
    radiant: None,
    working_folder: None,
    slicer_args_formatted: None,
    slicer_validator_args_formatted: None,
    slicer_all_args_formatted: None,
  ))
}

fn parse_status(status: String) -> types.RecordStatus {
  case status {
    "pending" -> types.Pending
    "inwork" -> types.InWork
    "finished" -> types.Finished
    "failed" -> types.Failed
    "pause" -> types.Paused
    _ -> types.Pending
  }
}

fn decode_records(data: dynamic.Dynamic) -> Result(List(Record), ApiError) {
  case decode.run(data, decode.list(record_decoder())) {
    Ok(records) -> Ok(records)
    Error(_) -> Error(types.ParseError("Invalid records data"))
  }
}
