// Study API endpoints
import api/http_client
import api/models.{type Study}
import api/types.{type ApiError}
import gleam/dynamic
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/option.{None}
import gleam/result

// Get all studies
pub fn get_studies() -> Promise(Result(List(Study), ApiError)) {
  http_client.get("/studies")
  |> promise.map(fn(res) { result.try(res, decode_studies) })
}

// Get a single study by UID
pub fn get_study(study_uid: String) -> Promise(Result(Study, ApiError)) {
  http_client.get("/studies/" <> study_uid)
  |> promise.map(fn(res) { result.try(res, decode_study) })
}

// Public decoder for reuse
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

fn decode_study(data: dynamic.Dynamic) -> Result(Study, ApiError) {
  case decode.run(data, study_decoder()) {
    Ok(study) -> Ok(study)
    Error(_) -> Error(types.ParseError("Invalid study data"))
  }
}

fn decode_studies(data: dynamic.Dynamic) -> Result(List(Study), ApiError) {
  case decode.run(data, decode.list(study_decoder())) {
    Ok(studies) -> Ok(studies)
    Error(_) -> Error(types.ParseError("Invalid studies data"))
  }
}
