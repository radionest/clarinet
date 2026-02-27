// Study API endpoints
import api/http_client
import api/models.{type Study}
import api/series
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/option.{None}
import gleam/result

// Get all studies
pub fn get_studies() -> Promise(Result(List(Study), ApiError)) {
  http_client.get("/studies")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(study_decoder()),
      "Invalid studies data",
    ))
  })
}

// Get a single study by UID (returns full detail with patient + series)
pub fn get_study(study_uid: String) -> Promise(Result(Study, ApiError)) {
  http_client.get("/studies/" <> study_uid)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      study_detail_decoder(),
      "Invalid study data",
    ))
  })
}

// Delete a study
pub fn delete_study(study_uid: String) -> Promise(Result(Nil, ApiError)) {
  http_client.delete("/studies/" <> study_uid)
  |> promise.map(fn(res) { result.map(res, fn(_) { Nil }) })
}

// Lightweight decoder (no nested relations) - used for lists
pub fn study_decoder() -> decode.Decoder(Study) {
  use study_uid <- decode.field("study_uid", decode.string)
  use date <- decode.field("date", decode.string)
  use anon_uid <- decode.optional_field("anon_uid", None, decode.optional(decode.string))
  use patient_id <- decode.field("patient_id", decode.string)

  decode.success(models.Study(
    study_uid: study_uid,
    date: date,
    anon_uid: anon_uid,
    patient_id: patient_id,
    patient: None,
    series: None,
    records: None,
  ))
}

// Detail decoder with nested patient and series
pub fn study_detail_decoder() -> decode.Decoder(Study) {
  use study_uid <- decode.field("study_uid", decode.string)
  use date <- decode.field("date", decode.string)
  use anon_uid <- decode.optional_field("anon_uid", None, decode.optional(decode.string))
  use patient_id <- decode.field("patient_id", decode.string)
  use patient <- decode.optional_field(
    "patient",
    None,
    decode.optional(patient_base_decoder()),
  )
  use study_series <- decode.optional_field(
    "series",
    None,
    decode.optional(decode.list(series.series_base_decoder())),
  )

  decode.success(models.Study(
    study_uid: study_uid,
    date: date,
    anon_uid: anon_uid,
    patient_id: patient_id,
    patient: patient,
    series: study_series,
    records: None,
  ))
}

// Inline patient decoder to avoid circular deps with patients.gleam
fn patient_base_decoder() -> decode.Decoder(models.Patient) {
  use id <- decode.field("id", decode.string)
  use name <- decode.optional_field("name", None, decode.optional(decode.string))
  use anon_id <- decode.optional_field("anon_id", None, decode.optional(decode.string))
  use anon_name <- decode.optional_field("anon_name", None, decode.optional(decode.string))
  use auto_id <- decode.optional_field("auto_id", None, decode.optional(decode.int))

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
