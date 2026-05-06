// Series API endpoints
import api/http_client
import api/models.{type Series}
import api/types.{type ApiError}
import utils/status
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/option.{None}
import gleam/result

// Get a single series by UID
pub fn get_series(series_uid: String) -> Promise(Result(Series, ApiError)) {
  http_client.get("/series/" <> series_uid)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      series_detail_decoder(),
      "Invalid series data",
    ))
  })
}

// Lightweight decoder (no nested relations) - exported for reuse by studies.gleam
pub fn series_base_decoder() -> decode.Decoder(Series) {
  use series_uid <- decode.field("series_uid", decode.string)
  use series_description <- decode.optional_field(
    "series_description",
    None,
    decode.optional(decode.string),
  )
  use series_number <- decode.field("series_number", decode.int)
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
  use anon_uid <- decode.optional_field("anon_uid", None, decode.optional(decode.string))
  use study_uid <- decode.field("study_uid", decode.string)
  use working_folder <- decode.optional_field(
    "working_folder",
    None,
    decode.optional(decode.string),
  )

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
    working_folder: working_folder,
  ))
}

// Detail decoder with nested study and records
fn series_detail_decoder() -> decode.Decoder(Series) {
  use series_uid <- decode.field("series_uid", decode.string)
  use series_description <- decode.optional_field(
    "series_description",
    None,
    decode.optional(decode.string),
  )
  use series_number <- decode.field("series_number", decode.int)
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
  use anon_uid <- decode.optional_field("anon_uid", None, decode.optional(decode.string))
  use study_uid <- decode.field("study_uid", decode.string)
  use working_folder <- decode.optional_field(
    "working_folder",
    None,
    decode.optional(decode.string),
  )
  use study <- decode.optional_field(
    "study",
    None,
    decode.optional(study_base_decoder()),
  )
  use records <- decode.optional_field(
    "records",
    None,
    decode.optional(decode.list(record_base_decoder())),
  )

  decode.success(models.Series(
    series_uid: series_uid,
    series_description: series_description,
    series_number: series_number,
    modality: modality,
    instance_count: instance_count,
    anon_uid: anon_uid,
    study_uid: study_uid,
    study: study,
    records: records,
    working_folder: working_folder,
  ))
}

// Inline study decoder to avoid circular deps with studies.gleam
fn study_base_decoder() -> decode.Decoder(models.Study) {
  use study_uid <- decode.field("study_uid", decode.string)
  use date <- decode.field("date", decode.string)
  use anon_uid <- decode.optional_field("anon_uid", None, decode.optional(decode.string))
  use study_description <- decode.optional_field("study_description", None, decode.optional(decode.string))
  use modalities_in_study <- decode.optional_field("modalities_in_study", None, decode.optional(decode.string))
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

// Inline record decoder to avoid circular deps with records.gleam
fn record_base_decoder() -> decode.Decoder(models.Record) {
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

  let status = status.from_backend_string(status_str)

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
    parent_record_id: None,
    study_anon_uid: None,
    series_anon_uid: None,
    viewer_study_uids: None,
    viewer_series_uids: None,
    clarinet_storage_path: None,
    files: None,
    file_checksums: None,
    file_links: None,
    patient: None,
    study: None,
    series: None,
    record_type: None,
    user: None,
    data: None,
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
