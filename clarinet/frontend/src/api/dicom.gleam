// DICOM API endpoints for PACS query and import
import api/http_client
import api/models.{
  type PacsSeriesResult, type PacsStudyResult, type PacsStudyWithSeries, type Study,
}
import api/studies
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/option.{None}
import gleam/result

// Search PACS for patient studies
pub fn search_patient_studies(
  patient_id: String,
) -> Promise(Result(List(PacsStudyWithSeries), ApiError)) {
  http_client.get("/dicom/patient/" <> patient_id <> "/studies")
  |> promise.map(fn(res) {
    result.try(
      res,
      http_client.decode_response(
        _,
        decode.list(pacs_study_with_series_decoder()),
        "Invalid PACS studies data",
      ),
    )
  })
}

// Import a study from PACS into local database
pub fn import_study(
  study_uid: String,
  patient_id: String,
) -> Promise(Result(Study, ApiError)) {
  let body =
    json.object([
      #("study_instance_uid", json.string(study_uid)),
      #("patient_id", json.string(patient_id)),
    ])
    |> json.to_string

  http_client.post("/dicom/import-study", body)
  |> promise.map(fn(res) {
    result.try(
      res,
      http_client.decode_response(
        _,
        studies.study_detail_decoder(),
        "Invalid imported study data",
      ),
    )
  })
}

// Decoders

fn pacs_study_result_decoder() -> decode.Decoder(PacsStudyResult) {
  use patient_id <- decode.optional_field(
    "patient_id",
    None,
    decode.optional(decode.string),
  )
  use patient_name <- decode.optional_field(
    "patient_name",
    None,
    decode.optional(decode.string),
  )
  use study_instance_uid <- decode.field("study_instance_uid", decode.string)
  use study_date <- decode.optional_field(
    "study_date",
    None,
    decode.optional(decode.string),
  )
  use study_time <- decode.optional_field(
    "study_time",
    None,
    decode.optional(decode.string),
  )
  use study_description <- decode.optional_field(
    "study_description",
    None,
    decode.optional(decode.string),
  )
  use accession_number <- decode.optional_field(
    "accession_number",
    None,
    decode.optional(decode.string),
  )
  use modalities_in_study <- decode.optional_field(
    "modalities_in_study",
    None,
    decode.optional(decode.string),
  )
  use number_of_study_related_series <- decode.optional_field(
    "number_of_study_related_series",
    None,
    decode.optional(decode.int),
  )
  use number_of_study_related_instances <- decode.optional_field(
    "number_of_study_related_instances",
    None,
    decode.optional(decode.int),
  )

  decode.success(models.PacsStudyResult(
    patient_id: patient_id,
    patient_name: patient_name,
    study_instance_uid: study_instance_uid,
    study_date: study_date,
    study_time: study_time,
    study_description: study_description,
    accession_number: accession_number,
    modalities_in_study: modalities_in_study,
    number_of_study_related_series: number_of_study_related_series,
    number_of_study_related_instances: number_of_study_related_instances,
  ))
}

fn pacs_series_result_decoder() -> decode.Decoder(PacsSeriesResult) {
  use study_instance_uid <- decode.field("study_instance_uid", decode.string)
  use series_instance_uid <- decode.field("series_instance_uid", decode.string)
  use series_number <- decode.optional_field(
    "series_number",
    None,
    decode.optional(decode.int),
  )
  use modality <- decode.optional_field(
    "modality",
    None,
    decode.optional(decode.string),
  )
  use series_description <- decode.optional_field(
    "series_description",
    None,
    decode.optional(decode.string),
  )
  use number_of_series_related_instances <- decode.optional_field(
    "number_of_series_related_instances",
    None,
    decode.optional(decode.int),
  )

  decode.success(models.PacsSeriesResult(
    study_instance_uid: study_instance_uid,
    series_instance_uid: series_instance_uid,
    series_number: series_number,
    modality: modality,
    series_description: series_description,
    number_of_series_related_instances: number_of_series_related_instances,
  ))
}

fn pacs_study_with_series_decoder() -> decode.Decoder(PacsStudyWithSeries) {
  use study <- decode.field("study", pacs_study_result_decoder())
  use series <- decode.field("series", decode.list(pacs_series_result_decoder()))
  use already_exists <- decode.field("already_exists", decode.bool)

  decode.success(models.PacsStudyWithSeries(
    study: study,
    series: series,
    already_exists: already_exists,
  ))
}
