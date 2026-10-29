// Global search API (POST /api/search)
import api/http_client
import api/models
import api/patients
import api/studies
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/result

pub type SearchResults {
  SearchResults(patients: List(models.Patient), studies: List(models.Study))
}

/// Run a global search: one query string resolved server-side across
/// patients and studies.
pub fn search(query: String, limit: Int) -> Promise(Result(SearchResults, ApiError)) {
  let body =
    json.object([
      #("query", json.string(query)),
      #("limit", json.int(limit)),
    ])
    |> json.to_string

  http_client.post("/search", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      search_results_decoder(),
      "Invalid search results",
    ))
  })
}

pub fn search_results_decoder() -> decode.Decoder(SearchResults) {
  use patients <- decode.field("patients", decode.list(patients.patient_decoder()))
  use studies <- decode.field("studies", decode.list(studies.study_detail_decoder()))
  decode.success(SearchResults(patients: patients, studies: studies))
}
