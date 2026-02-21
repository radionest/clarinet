/// HTTP client module for the Clarinet frontend.
///
/// Provides a simple, type-safe interface for making HTTP requests to the backend API.
/// Uses gleam_fetch for networking with automatic cookie handling for session-based authentication.
/// All requests automatically include JSON content-type headers and prefix paths with "/api".
import api/types.{type ApiError}
import gleam/dynamic.{type Dynamic}
import gleam/dynamic/decode
import gleam/fetch
import gleam/http
import gleam/http/request
import gleam/http/response.{type Response}
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/uri
import multipart_form
import multipart_form/field.{type FormBody}
import plinth/browser/window

/// Builds an HTTP request with standard headers and API prefix.
/// Attempts to use the browser's origin as base URL, falling back to relative paths.
fn build_request(
  method: http.Method,
  path: String,
  body: Option(String),
) -> request.Request(String) {
  let req =
    {
      // Try to get browser origin for absolute URLs, fallback to relative
      use origin <- result.try(window.origin() |> uri.parse)
      request.from_uri(origin)
    }
    |> result.unwrap(request.new())
    |> request.set_method(method)
    |> request.set_path("/api" <> path)
    |> request.set_header("content-type", "application/json")
    |> request.set_header("accept", "application/json")

  case body {
    Some(json_body) -> request.set_body(req, json_body)
    None -> request.set_body(req, "")
  }
}

/// Builds an HTTP request with multipart/form-data body.
/// Used for form submissions that need to send data as form fields rather than JSON.
fn build_multipart_request(
  method: http.Method,
  path: String,
  form: List(#(String, FormBody)),
) -> request.Request(BitArray) {
  let req =
    {
      // Try to get browser origin for absolute URLs, fallback to relative
      use origin <- result.try(window.origin() |> uri.parse)
      request.from_uri(origin)
    }
    |> result.unwrap(request.new())
    |> request.set_method(method)
    |> request.set_path("/api" <> path)
    |> request.set_header("content-type", "multipart/form-data")
    |> request.set_header("accept", "application/json")
    |> multipart_form.to_request(form)

  req
}

/// Processes HTTP responses, converting status codes to appropriate errors.
/// Handles async body reading for successful responses with JSON parsing.
/// Returns Dynamic for flexibility - callers decode to specific types.
pub fn process_response(
  response: response.Response(fetch.FetchBody),
) -> Promise(Result(Dynamic, ApiError)) {
  case response.status {
    200 | 201 -> {
      // Success - parse JSON response body
      use body_result <- promise.map(fetch.read_text_body(response))
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
    204 -> {
      // No content - return nil as success
      promise.resolve(Ok(dynamic.nil()))
    }
    401 -> promise.resolve(Error(types.AuthError("Unauthorized")))
    403 -> promise.resolve(Error(types.AuthError("Forbidden")))
    404 -> promise.resolve(Error(types.ServerError(404, "Not Found")))
    400 -> promise.resolve(Error(types.ValidationError([])))
    code -> promise.resolve(Error(types.ServerError(code, "Server error")))
  }
}

/// Makes an HTTP request with optional JSON body.
/// Orchestrates request building, sending, and response processing.
/// Network errors are caught and converted to ApiError types.
pub fn request_with_body(
  method method: http.Method,
  path path: String,
  body body: Option(String),
) -> Promise(Result(Dynamic, ApiError)) {
  let req = build_request(method, path, body)

  use resp_result <- promise.await(fetch.send(req))
  case resp_result {
    Error(fetch.NetworkError(msg)) ->
      promise.resolve(Error(types.NetworkError(msg)))
    Error(_) -> promise.resolve(Error(types.NetworkError("Request failed")))
    Ok(response) -> process_response(response)
  }
}

/// Performs a GET request to the specified API path.
pub fn get(path: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(http.Get, path, None)
}

/// Performs a POST request with a JSON body to the specified API path.
pub fn post(path: String, body: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(http.Post, path, Some(body))
}

/// Performs a POST request with multipart/form-data to the specified API path.
/// Takes the multipart body and boundary string for proper content-type header.
pub fn post_multipart(
  path: String,
  form: List(#(String, FormBody)),
) -> Promise(Result(Dynamic, ApiError)) {
  let req = build_multipart_request(http.Post, path, form)

  use resp_result <- promise.await(fetch.send_bits(req))
  case resp_result {
    Error(fetch.NetworkError(msg)) ->
      promise.resolve(Error(types.NetworkError(msg)))
    Error(_) -> promise.resolve(Error(types.NetworkError("Request failed")))
    Ok(response) -> process_response(response)
  }
}

/// Performs a PATCH request with a JSON body to the specified API path.
pub fn patch(path: String, body: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(http.Patch, path, Some(body))
}

/// Performs a PUT request with a JSON body to the specified API path.
pub fn put(path: String, body: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(http.Put, path, Some(body))
}

/// Performs a DELETE request to the specified API path.
pub fn delete(path: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(http.Delete, path, None)
}
