// DICOMweb preload API endpoints

import api/types.{type ApiError}
import config
import gleam/dynamic.{type Dynamic}
import gleam/dynamic/decode
import gleam/fetch
import gleam/http
import gleam/http/request
import gleam/http/response
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/result
import gleam/uri
import plinth/browser/window

/// Base request without /api prefix (DICOMweb is at /dicom-web).
fn dicomweb_request(
  method: http.Method,
  path: String,
) -> request.Request(String) {
  {
    use origin <- result.try(window.origin() |> uri.parse)
    request.from_uri(origin)
  }
  |> result.unwrap(request.new())
  |> request.set_method(method)
  |> request.set_path(config.base_path() <> "/dicom-web" <> path)
  |> request.set_header("accept", "application/json")
  |> request.set_header("content-type", "application/json")
  |> request.set_body("")
}

fn process_response(
  resp: response.Response(fetch.FetchBody),
) -> Promise(Result(Dynamic, ApiError)) {
  case resp.status {
    200 | 201 -> {
      use body_result <- promise.map(fetch.read_text_body(resp))
      case body_result {
        Ok(text_response) -> {
          case json.parse(text_response.body, decode.dynamic) {
            Ok(data) -> Ok(data)
            Error(_) -> Error(types.ParseError("Invalid JSON"))
          }
        }
        Error(_) -> Error(types.ParseError("Failed to read body"))
      }
    }
    401 -> promise.resolve(Error(types.AuthError("Unauthorized")))
    code -> promise.resolve(Error(types.ServerError(code, "Server error")))
  }
}

/// POST /dicom-web/preload/{study_uid} — start background preload.
pub fn preload_study(
  study_uid: String,
) -> Promise(Result(Dynamic, ApiError)) {
  let req = dicomweb_request(http.Post, "/preload/" <> study_uid)

  use resp_result <- promise.await(fetch.send(req))
  case resp_result {
    Error(fetch.NetworkError(msg)) ->
      promise.resolve(Error(types.NetworkError(msg)))
    Error(_) -> promise.resolve(Error(types.NetworkError("Request failed")))
    Ok(response) -> process_response(response)
  }
}

/// GET /dicom-web/preload/{study_uid}/progress/{task_id} — poll progress.
pub fn preload_progress(
  study_uid: String,
  task_id: String,
) -> Promise(Result(Dynamic, ApiError)) {
  let req =
    dicomweb_request(
      http.Get,
      "/preload/" <> study_uid <> "/progress/" <> task_id,
    )

  use resp_result <- promise.await(fetch.send(req))
  case resp_result {
    Error(fetch.NetworkError(msg)) ->
      promise.resolve(Error(types.NetworkError(msg)))
    Error(_) -> promise.resolve(Error(types.NetworkError("Request failed")))
    Ok(response) -> process_response(response)
  }
}
