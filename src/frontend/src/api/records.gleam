// Record API endpoints
import api/http_client
import api/models.{type Record, type RecordType, type User}
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/option.{None, Some}
import gleam/result
import utils/json_utils

// Get all records
pub fn get_records() -> Promise(Result(List(Record), ApiError)) {
  http_client.get("/records/")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(record_decoder()),
      "Invalid records data",
    ))
  })
}

// Get current user's records
pub fn get_my_records() -> Promise(Result(List(Record), ApiError)) {
  http_client.get("/records/my")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(record_decoder()),
      "Invalid records data",
    ))
  })
}

// Get a single record by ID
pub fn get_record(id: String) -> Promise(Result(Record, ApiError)) {
  http_client.get("/records/" <> id)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

// Decoder for nested RecordType (RecordTypeBase from backend)
fn record_type_base_decoder() -> decode.Decoder(RecordType) {
  use name <- decode.field("name", decode.string)
  use description <- decode.optional_field(
    "description",
    None,
    decode.optional(decode.string),
  )
  use label <- decode.optional_field("label", None, decode.optional(decode.string))
  use level_str <- decode.optional_field(
    "level",
    None,
    decode.optional(decode.string),
  )
  use data_schema_dyn <- decode.optional_field(
    "data_schema",
    None,
    decode.optional(decode.dynamic),
  )

  let level = case level_str {
    None -> types.Series
    Some("patient") -> types.Patient
    Some("study") -> types.Study
    Some("series") -> types.Series
    Some(_) -> types.Series
  }

  let data_schema = case data_schema_dyn {
    Some(dyn) -> Some(json_utils.dynamic_to_string(dyn))
    None -> None
  }

  decode.success(models.RecordType(
    name: name,
    description: description,
    label: label,
    slicer_script: None,
    slicer_script_args: None,
    slicer_result_validator: None,
    slicer_result_validator_args: None,
    data_schema: data_schema,
    role_name: None,
    max_users: None,
    min_users: None,
    level: level,
    input_files: None,
    output_files: None,
    constraint_role: None,
    records: None,
  ))
}

// Decoder for nested User
fn user_base_decoder() -> decode.Decoder(User) {
  use id <- decode.field("id", decode.string)
  use email <- decode.field("email", decode.string)
  use is_active <- decode.optional_field("is_active", True, decode.bool)
  use is_superuser <- decode.optional_field("is_superuser", False, decode.bool)
  use is_verified <- decode.optional_field("is_verified", False, decode.bool)

  decode.success(models.User(
    id: id,
    email: email,
    is_active: is_active,
    is_superuser: is_superuser,
    is_verified: is_verified,
  ))
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
  use record_type <- decode.optional_field(
    "record_type",
    None,
    decode.optional(record_type_base_decoder()),
  )
  use user <- decode.optional_field(
    "user",
    None,
    decode.optional(user_base_decoder()),
  )
  use created_at <- decode.optional_field(
    "created_at",
    None,
    decode.optional(decode.string),
  )
  use changed_at <- decode.optional_field(
    "changed_at",
    None,
    decode.optional(decode.string),
  )
  use data_dyn <- decode.optional_field(
    "data",
    None,
    decode.optional(decode.dynamic),
  )

  let status = parse_status(status_str)
  let data = case data_dyn {
    Some(dyn) -> Some(json_utils.dynamic_to_string(dyn))
    None -> None
  }

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
    record_type: record_type,
    user: user,
    data: data,
    created_at: created_at,
    changed_at: changed_at,
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
