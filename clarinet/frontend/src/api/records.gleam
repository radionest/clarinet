// Record API endpoints
import api/http_client
import api/models.{
  type FileDefinition, type Record, type RecordCreate, type RecordFilterOptions,
  type RecordType,
}
import api/record_page.{type RecordPage}
import api/types.{type ApiError}
import gleam/dict
import gleam/dynamic
import gleam/dynamic/decode
import gleam/int
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/uri
import utils/json_utils
import utils/status

// Search records with cursor-based pagination
pub fn find_records(
  filter_fields: List(#(String, json.Json)),
  cursor: Option(String),
  limit: Int,
) -> Promise(Result(RecordPage, ApiError)) {
  let cursor_field = case cursor {
    Some(c) -> [#("cursor", json.string(c))]
    None -> []
  }
  let body =
    json.object(
      list.flatten([filter_fields, cursor_field, [#("limit", json.int(limit))]]),
    )
    |> json.to_string
  http_client.post("/records/find", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_page.decoder(record_decoder()),
      "Invalid page data",
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

// Build the URL used as `<a href download>` to fetch an OUTPUT file.
// Cookie auth is automatic; the backend's `Content-Disposition: attachment`
// triggers the browser's native download flow.
pub fn output_file_download_url(record_id: String, file_name: String) -> String {
  http_client.api_url("/records/" <> record_id <> "/output-files/" <> file_name)
}

// Decoder for nested RecordType (RecordTypeBase from backend)
fn record_type_base_decoder() -> decode.Decoder(RecordType) {
  use name <- decode.field("name", decode.string)
  use description <- decode.optional_field(
    "description",
    None,
    decode.optional(decode.string),
  )
  use label <- decode.optional_field(
    "label",
    None,
    decode.optional(decode.string),
  )
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
  use ui_schema_dyn <- decode.optional_field(
    "ui_schema",
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
  use parent_required <- decode.optional_field(
    "parent_required",
    False,
    decode.bool,
  )
  use inherit_user_from_parent <- decode.optional_field(
    "inherit_user_from_parent",
    False,
    decode.bool,
  )
  use editable <- decode.optional_field("editable", True, decode.bool)
  use edit_window_days <- decode.optional_field(
    "edit_window_days",
    None,
    decode.optional(decode.int),
  )
  use viewer_mode <- decode.optional_field(
    "viewer_mode",
    "single_series",
    decode.string,
  )
  use file_registry <- decode.optional_field(
    "file_registry",
    None,
    decode.optional(decode.list(file_definition_decoder())),
  )
  use allowed_viewers <- decode.optional_field(
    "allowed_viewers",
    None,
    decode.optional(decode.list(decode.string)),
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

  let ui_schema = case ui_schema_dyn {
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
    ui_schema: ui_schema,
    role_name: role_name,
    max_records: None,
    min_records: None,
    unique_per_user: unique_per_user,
    parent_required: parent_required,
    inherit_user_from_parent: inherit_user_from_parent,
    editable: editable,
    edit_window_days: edit_window_days,
    viewer_mode: viewer_mode,
    allowed_viewers: allowed_viewers,
    level: level,
    file_registry: file_registry,
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
  ))
}

// Inline patient decoder to avoid circular deps with patients.gleam
fn patient_base_decoder() -> decode.Decoder(models.Patient) {
  use id <- decode.field("id", decode.string)
  use name <- decode.optional_field(
    "name",
    None,
    decode.optional(decode.string),
  )
  use anon_id <- decode.optional_field(
    "anon_id",
    None,
    decode.optional(decode.string),
  )
  use anon_name <- decode.optional_field(
    "anon_name",
    None,
    decode.optional(decode.string),
  )
  use auto_id <- decode.optional_field(
    "auto_id",
    None,
    decode.optional(decode.int),
  )

  decode.success(models.Patient(
    id: id,
    name: name,
    anon_id: anon_id,
    anon_name: anon_name,
    auto_id: auto_id,
    studies: None,
    records: None,
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
  use context_info_html <- decode.optional_field(
    "context_info_html",
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
  use patient <- decode.optional_field(
    "patient",
    None,
    decode.optional(patient_base_decoder()),
  )
  use file_links <- decode.optional_field(
    "file_links",
    None,
    decode.optional(decode.list(record_file_link_decoder())),
  )
  use is_editable <- decode.optional_field("is_editable", True, decode.bool)
  use shared_editing <- decode.optional_field("shared_editing", False, decode.bool)
  use display_anon_id <- decode.optional_field(
    "display_anon_id",
    None,
    decode.optional(decode.string),
  )

  let status = status.from_backend_string(status_str)
  let data = case data_dyn {
    Some(dyn) -> Some(json_utils.dynamic_to_string(dyn))
    None -> None
  }

  decode.success(models.Record(
    id: id,
    context_info: context_info,
    context_info_html: context_info_html,
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
    file_links: file_links,
    patient: patient,
    study: study,
    series: series,
    record_type: record_type,
    data: data,
    created_at: created_at,
    changed_at: changed_at,
    started_at: started_at,
    finished_at: finished_at,
    radiant: None,
    display_anon_id: display_anon_id,
    is_editable: is_editable,
    shared_editing: shared_editing,
  ))
}

// Decoder for RecordFileLink (per-file link on a record)
fn record_file_link_decoder() -> decode.Decoder(models.RecordFileLink) {
  use name <- decode.field("name", decode.string)
  use filename <- decode.field("filename", decode.string)
  use checksum <- decode.optional_field(
    "checksum",
    None,
    decode.optional(decode.string),
  )
  decode.success(models.RecordFileLink(
    name: name,
    filename: filename,
    checksum: checksum,
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
  use label <- decode.optional_field(
    "label",
    None,
    decode.optional(decode.string),
  )
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
  use ui_schema_dyn <- decode.optional_field(
    "ui_schema",
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
  use parent_required <- decode.optional_field(
    "parent_required",
    False,
    decode.bool,
  )
  use inherit_user_from_parent <- decode.optional_field(
    "inherit_user_from_parent",
    False,
    decode.bool,
  )
  use editable <- decode.optional_field("editable", True, decode.bool)
  use edit_window_days <- decode.optional_field(
    "edit_window_days",
    None,
    decode.optional(decode.int),
  )
  use viewer_mode <- decode.optional_field(
    "viewer_mode",
    "single_series",
    decode.string,
  )
  use file_registry <- decode.optional_field(
    "file_registry",
    None,
    decode.optional(decode.list(file_definition_decoder())),
  )
  use allowed_viewers <- decode.optional_field(
    "allowed_viewers",
    None,
    decode.optional(decode.list(decode.string)),
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

  let ui_schema = case ui_schema_dyn {
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
    ui_schema: ui_schema,
    role_name: role_name,
    max_records: max_records,
    min_records: min_records,
    unique_per_user: unique_per_user,
    parent_required: parent_required,
    inherit_user_from_parent: inherit_user_from_parent,
    editable: editable,
    edit_window_days: edit_window_days,
    viewer_mode: viewer_mode,
    allowed_viewers: allowed_viewers,
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
    "/records/" <> int.to_string(record_id) <> "/user?user_id=" <> user_id
  http_client.patch(path, json.to_string(json.object([])))
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Record types the current user can still take from the pool, mapped to the
/// count of pending records per type (GET /records/available_types).
pub fn get_available_types() -> Promise(
  Result(dict.Dict(String, Int), ApiError),
) {
  http_client.get("/records/available_types")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.dict(decode.string, decode.int),
      "Invalid available types data",
    ))
  })
}

/// Claim a random unassigned pending record of `record_type_name` from the pool
/// (POST /records/claim-next). The record is assigned to the caller and set to
/// inwork; a 404 means the pool holds no claimable record of that type.
pub fn claim_next(record_type_name: String) -> Promise(Result(Record, ApiError)) {
  let path =
    "/records/claim-next?record_type_name="
    <> uri.percent_encode(record_type_name)
  http_client.post(path, json.to_string(json.object([])))
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
pub fn submit_record(record_id: String) -> Promise(Result(Record, ApiError)) {
  http_client.post_with_slicer_context(
    "/records/" <> record_id <> "/submit",
    "{}",
  )
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Re-submit record with server-side Slicer validation (PATCH /submit)
pub fn resubmit_record(record_id: String) -> Promise(Result(Record, ApiError)) {
  http_client.patch_with_slicer_context(
    "/records/" <> record_id <> "/submit",
    "{}",
  )
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Restart an auto task by invalidating it (hard mode)
pub fn restart_record(record_id: String) -> Promise(Result(Record, ApiError)) {
  let body =
    json.object([
      #("mode", json.string("hard")),
      #("reason", json.string("Manually restarted")),
    ])
  http_client.post(
    "/records/" <> record_id <> "/invalidate",
    json.to_string(body),
  )
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

/// Admin-only cascade delete of a record, its descendants and output files.
/// Returns 409 if any record in the subtree is inwork.
pub fn delete_record(id: String) -> Promise(Result(Nil, ApiError)) {
  http_client.delete("/admin/records/" <> id)
  |> promise.map(fn(res) { result.map(res, fn(_) { Nil }) })
}

/// Manually fail a record with a reason
pub fn fail_record(
  record_id: String,
  reason: String,
) -> Promise(Result(Record, ApiError)) {
  let body = json.object([#("reason", json.string(reason))])
  http_client.post("/records/" <> record_id <> "/fail", json.to_string(body))
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

/// Get all record types
pub fn get_record_types() -> Promise(Result(List(RecordType), ApiError)) {
  http_client.get("/records/types")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(record_type_base_decoder()),
      "Invalid record types data",
    ))
  })
}

/// Distinct values for filter dropdowns on /records and /admin.
/// Backend ignores any UI filters in the body and always returns the
/// caller's full RBAC scope.
pub fn get_filter_options() -> Promise(Result(RecordFilterOptions, ApiError)) {
  http_client.post("/records/filter-options", "{}")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      filter_options_decoder(),
      "Invalid filter options data",
    ))
  })
}

fn filter_options_decoder() -> decode.Decoder(RecordFilterOptions) {
  use patients <- decode.field("patients", decode.list(decode.string))
  use record_types <- decode.field("record_types", decode.list(decode.string))
  use users <- decode.field("users", decode.list(decode.string))
  decode.success(models.RecordFilterOptions(
    patients: patients,
    record_types: record_types,
    users: users,
  ))
}

/// Create a new record
pub fn create_record(data: RecordCreate) -> Promise(Result(Record, ApiError)) {
  let body =
    json.object([
      #("record_type_name", json.string(data.record_type_name)),
      #("patient_id", json.string(data.patient_id)),
      #("status", json.string(status.to_backend_string(data.status))),
      #("study_uid", json_nullable_string(data.study_uid)),
      #("series_uid", json_nullable_string(data.series_uid)),
      #("user_id", json_nullable_string(data.user_id)),
      #("parent_record_id", case data.parent_record_id {
        Some(id) -> json.int(id)
        None -> json.null()
      }),
      #("context_info", json_nullable_string(data.context_info)),
    ])
    |> json.to_string

  http_client.post("/records/", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_decoder(),
      "Invalid record data",
    ))
  })
}

fn json_nullable_string(value: Option(String)) -> json.Json {
  case value {
    Some(v) -> json.string(v)
    None -> json.null()
  }
}

/// Get a single record type by name
pub fn get_record_type(name: String) -> Promise(Result(RecordType, ApiError)) {
  http_client.get("/records/types/" <> uri.percent_encode(name))
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      record_type_full_decoder(),
      "Invalid record type data",
    ))
  })
}
