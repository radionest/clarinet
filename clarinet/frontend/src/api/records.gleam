// Record API endpoints
import api/http_client
import api/models.{type FileDefinition, type Record, type RecordType}
import api/types.{type ApiError}
import api/users
import utils/status
import gleam/dict
import gleam/dynamic
import gleam/dynamic/decode
import gleam/int
import gleam/javascript/promise.{type Promise}
import gleam/json
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
  use slicer_script <- decode.optional_field(
    "slicer_script",
    None,
    decode.optional(decode.string),
  )
  use slicer_result_validator <- decode.optional_field(
    "slicer_result_validator",
    None,
    decode.optional(decode.string),
  )
  use role_name <- decode.optional_field(
    "role_name",
    None,
    decode.optional(decode.string),
  )
  use unique_per_user <- decode.optional_field(
    "unique_per_user",
    False,
    decode.bool,
  )

  let level = case level_str {
    None -> types.Series
    Some("patient") | Some("PATIENT") -> types.Patient
    Some("study") | Some("STUDY") -> types.Study
    Some("series") | Some("SERIES") -> types.Series
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
    slicer_script: slicer_script,
    slicer_script_args: None,
    slicer_result_validator: slicer_result_validator,
    slicer_result_validator_args: None,
    data_schema: data_schema,
    role_name: role_name,
    max_records: None,
    min_records: None,
    unique_per_user: unique_per_user,
    level: level,
    file_registry: None,
    constraint_role: None,
    records: None,
  ))
}

// Inline study decoder to avoid circular deps with studies.gleam
fn study_base_decoder() -> decode.Decoder(models.Study) {
  use study_uid <- decode.field("study_uid", decode.string)
  use date <- decode.field("date", decode.string)
  use anon_uid <- decode.optional_field(
    "anon_uid",
    None,
    decode.optional(decode.string),
  )
  use study_description <- decode.optional_field(
    "study_description",
    None,
    decode.optional(decode.string),
  )
  use modalities_in_study <- decode.optional_field(
    "modalities_in_study",
    None,
    decode.optional(decode.string),
  )
  use patient_id <- decode.field("patient_id", decode.string)

  decode.success(models.Study(
    study_uid: study_uid,
    date: date,
    anon_uid: anon_uid,
    study_description: study_description,
    modalities_in_study: modalities_in_study,
    patient_id: patient_id,
    patient: None,
    series: None,
    records: None,
  ))
}

// Inline series decoder to avoid circular deps with series.gleam
fn series_base_decoder() -> decode.Decoder(models.Series) {
  use series_uid <- decode.field("series_uid", decode.string)
  use series_description <- decode.optional_field(
    "series_description",
    None,
    decode.optional(decode.string),
  )
  use series_number <- decode.optional_field("series_number", 0, decode.int)
  use modality <- decode.optional_field(
    "modality",
    None,
    decode.optional(decode.string),
  )
  use instance_count <- decode.optional_field(
    "instance_count",
    None,
    decode.optional(decode.int),
  )
  use anon_uid <- decode.optional_field(
    "anon_uid",
    None,
    decode.optional(decode.string),
  )
  use study_uid <- decode.field("study_uid", decode.string)

  decode.success(models.Series(
    series_uid: series_uid,
    series_description: series_description,
    series_number: series_number,
    modality: modality,
    instance_count: instance_count,
    anon_uid: anon_uid,
    study_uid: study_uid,
    study: None,
    records: None,
    working_folder: None,
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
  use parent_record_id <- decode.optional_field(
    "parent_record_id",
    None,
    decode.optional(decode.int),
  )
  use record_type <- decode.optional_field(
    "record_type",
    None,
    decode.optional(record_type_base_decoder()),
  )
  use user <- decode.optional_field(
    "user",
    None,
    decode.optional(users.user_decoder()),
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
  use started_at <- decode.optional_field(
    "started_at",
    None,
    decode.optional(decode.string),
  )
  use finished_at <- decode.optional_field(
    "finished_at",
    None,
    decode.optional(decode.string),
  )
  use viewer_study_uids <- decode.optional_field(
    "viewer_study_uids",
    None,
    decode.optional(decode.list(decode.string)),
  )
  use viewer_series_uids <- decode.optional_field(
    "viewer_series_uids",
    None,
    decode.optional(decode.list(decode.string)),
  )
  use data_dyn <- decode.optional_field(
    "data",
    None,
    decode.optional(decode.dynamic),
  )
  use study <- decode.optional_field(
    "study",
    None,
    decode.optional(study_base_decoder()),
  )
  use series <- decode.optional_field(
    "series",
    None,
    decode.optional(series_base_decoder()),
  )

  let status = status.from_backend_string(status_str)
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
    parent_record_id: parent_record_id,
    study_anon_uid: None,
    series_anon_uid: None,
    viewer_study_uids: viewer_study_uids,
    viewer_series_uids: viewer_series_uids,
    clarinet_storage_path: None,
    files: None,
    file_checksums: None,
    patient: None,
    study: study,
    series: series,
    record_type: record_type,
    user: user,
    data: data,
    created_at: created_at,
    changed_at: changed_at,
    started_at: started_at,
    finished_at: finished_at,
    radiant: None,
    working_folder: None,
    slicer_args_formatted: None,
    slicer_validator_args_formatted: None,
    slicer_all_args_formatted: None,
  ))
}

// Decoder for FileDefinition
fn file_definition_decoder() -> decode.Decoder(FileDefinition) {
  use name <- decode.field("name", decode.string)
  use pattern <- decode.field("pattern", decode.string)
  use description <- decode.optional_field(
    "description",
    None,
    decode.optional(decode.string),
  )
  use required <- decode.optional_field("required", True, decode.bool)
  use multiple <- decode.optional_field("multiple", False, decode.bool)
  use role_str <- decode.optional_field("role", "output", decode.string)
  let role = case role_str {
    "input" -> models.Input
    "intermediate" -> models.Intermediate
    _ -> models.Output
  }
  use level <- decode.optional_field(
    "level",
    None,
    decode.optional(decode.string),
  )
  decode.success(models.FileDefinition(
    name: name,
    pattern: pattern,
    description: description,
    required: required,
    multiple: multiple,
    role: role,
    level: level,
  ))
}

// Decoder for dict(String, String) from dynamic
fn string_dict_decoder() -> decode.Decoder(dict.Dict(String, String)) {
  decode.dict(decode.string, decode.string)
}

/// Full RecordType decoder with all fields (for edit page)
pub fn record_type_full_decoder() -> decode.Decoder(RecordType) {
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
  use slicer_script <- decode.optional_field(
    "slicer_script",
    None,
    decode.optional(decode.string),
  )
  use slicer_result_validator <- decode.optional_field(
    "slicer_result_validator",
    None,
    decode.optional(decode.string),
  )
  use slicer_script_args <- decode.optional_field(
    "slicer_script_args",
    None,
    decode.optional(string_dict_decoder()),
  )
  use slicer_result_validator_args <- decode.optional_field(
    "slicer_result_validator_args",
    None,
    decode.optional(string_dict_decoder()),
  )
  use data_schema_dyn <- decode.optional_field(
    "data_schema",
    None,
    decode.optional(decode.dynamic),
  )
  use role_name <- decode.optional_field(
    "role_name",
    None,
    decode.optional(decode.string),
  )
  use max_records <- decode.optional_field(
    "max_records",
    None,
    decode.optional(decode.int),
  )
  use min_records <- decode.optional_field(
    "min_records",
    None,
    decode.optional(decode.int),
  )
  use unique_per_user <- decode.optional_field(
    "unique_per_user",
    False,
    decode.bool,
  )
  use file_registry <- decode.optional_field(
    "file_registry",
    None,
    decode.optional(decode.list(file_definition_decoder())),
  )
  use constraint_role_name <- decode.optional_field(
    "constraint_role",
    None,
    decode.optional(decode.string),
  )
  let level = case level_str {
    None -> types.Series
    Some("patient") | Some("PATIENT") -> types.Patient
    Some("study") | Some("STUDY") -> types.Study
    Some("series") | Some("SERIES") -> types.Series
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
    slicer_script: slicer_script,
    slicer_script_args: slicer_script_args,
    slicer_result_validator: slicer_result_validator,
    slicer_result_validator_args: slicer_result_validator_args,
    data_schema: data_schema,
    role_name: role_name,
    max_records: max_records,
    min_records: min_records,
    unique_per_user: unique_per_user,
    level: level,
    file_registry: file_registry,
    constraint_role: constraint_role_name,
    records: None,
  ))
}

/// Assign a user to a record (sets status to inwork)
pub fn assign_record_user(
  record_id: Int,
  user_id: String,
) -> Promise(Result(Record, ApiError)) {
  let path =
    "/records/"
    <> int.to_string(record_id)
    <> "/user?user_id="
    <> user_id
  http_client.patch(path, json.to_string(json.object([])))
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}


/// Submit empty data for a record without data_schema (slicer completion)
pub fn submit_record_data(
  record_id: String,
) -> Promise(Result(Record, ApiError)) {
  http_client.post("/records/" <> record_id <> "/data", "{}")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Re-submit data for a finished record without data_schema (PATCH)
pub fn resubmit_record_data(
  record_id: String,
) -> Promise(Result(Record, ApiError)) {
  http_client.patch("/records/" <> record_id <> "/data", "{}")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Submit record with server-side Slicer validation (POST /submit)
pub fn submit_record(
  record_id: String,
) -> Promise(Result(Record, ApiError)) {
  http_client.post("/records/" <> record_id <> "/submit", "{}")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Re-submit record with server-side Slicer validation (PATCH /submit)
pub fn resubmit_record(
  record_id: String,
) -> Promise(Result(Record, ApiError)) {
  http_client.patch("/records/" <> record_id <> "/submit", "{}")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Restart an auto task by invalidating it (hard mode)
pub fn restart_record(
  record_id: String,
) -> Promise(Result(Record, ApiError)) {
  let body =
    json.object([
      #("mode", json.string("hard")),
      #("reason", json.string("Manually restarted")),
    ])
  http_client.post("/records/" <> record_id <> "/invalidate", json.to_string(body))
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Get hydrated schema for a record (x-options resolved to oneOf)
pub fn get_hydrated_schema(
  record_id: String,
) -> Promise(Result(String, ApiError)) {
  http_client.get("/records/" <> record_id <> "/schema")
  |> promise.map(fn(res) {
    let nil_value = dynamic.nil()
    case res {
      Ok(data) if data == nil_value ->
        Error(types.ServerError(204, "No schema"))
      Ok(data) -> Ok(json_utils.dynamic_to_string(data))
      Error(err) -> Error(err)
    }
  })
}

/// Get a single record type by name
pub fn get_record_type(name: String) -> Promise(Result(RecordType, ApiError)) {
  http_client.get("/records/types/" <> name)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_type_full_decoder(),
      "Invalid record type data",
    ))
  })
}
