// Patient API endpoints
import api/http_client
import api/models.{type Patient}
import api/studies
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/option.{None}
import gleam/result

// Get all patients
pub fn get_patients() -> Promise(Result(List(Patient), ApiError)) {
  http_client.get("/patients")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(patient_decoder()),
      "Invalid patients data",
    ))
  })
}

// Get a single patient by ID
pub fn get_patient(id: String) -> Promise(Result(Patient, ApiError)) {
  http_client.get("/patients/" <> id)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      patient_decoder(),
      "Invalid patient data",
    ))
  })
}

// Create a new patient
pub fn create_patient(
  patient_id: String,
  patient_name: String,
) -> Promise(Result(Patient, ApiError)) {
  let body =
    json.object([
      #("patient_id", json.string(patient_id)),
      #("patient_name", json.string(patient_name)),
    ])
    |> json.to_string

  http_client.post("/patients", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      patient_decoder(),
      "Invalid patient data",
    ))
  })
}

// Anonymize a patient
pub fn anonymize_patient(id: String) -> Promise(Result(Patient, ApiError)) {
  http_client.post("/patients/" <> id <> "/anonymize", "{}")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      patient_decoder(),
      "Invalid patient data",
    ))
  })
}

// Delete a patient
pub fn delete_patient(id: String) -> Promise(Result(Nil, ApiError)) {
  http_client.delete("/patients/" <> id)
  |> promise.map(fn(res) { result.map(res, fn(_) { Nil }) })
}

// Patient decoder
pub fn patient_decoder() -> decode.Decoder(Patient) {
  use id <- decode.field("id", decode.string)
  use name <- decode.optional_field("name", None, decode.optional(decode.string))
  use anon_id <- decode.optional_field("anon_id", None, decode.optional(decode.string))
  use anon_name <- decode.optional_field("anon_name", None, decode.optional(decode.string))
  use auto_id <- decode.optional_field("auto_id", None, decode.optional(decode.int))
  use patient_studies <- decode.optional_field(
    "studies",
    None,
    decode.optional(decode.list(studies.study_decoder())),
  )

  decode.success(models.Patient(
    id: id,
    name: name,
    anon_id: anon_id,
    anon_name: anon_name,
    auto_id: auto_id,
    studies: patient_studies,
    records: None,
  ))
}
