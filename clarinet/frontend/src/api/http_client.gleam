/// HTTP client module for the Clarinet frontend.
///
/// Provides a simple, type-safe interface for making HTTP requests to the backend API.
/// Uses gleam_fetch for networking with automatic cookie handling for session-based authentication.
/// All requests automatically include JSON content-type headers and prefix paths with "/api".
import api/types.{type ApiError}
import config
import gleam/dict.{type Dict}
import gleam/dynamic.{type Dynamic}
import gleam/dynamic/decode
import gleam/fetch
import gleam/http
import gleam/http/request
import gleam/http/response
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/uri
import multipart_form
import multipart_form/field.{type FormBody}
import plinth/browser/window

/// Creates a base request with origin resolution, method, path prefix, and accept header.
fn base_request(method: http.Method, path: String) -> request.Request(String) {
  {
    use origin <- result.try(window.origin() |> uri.parse)
    request.from_uri(origin)
  }
  |> result.unwrap(request.new())
  |> request.set_method(method)
  |> request.set_path(config.base_path() <> "/api" <> path)
  |> request.set_header("accept", "application/json")
}

/// Builds an HTTP request with JSON content-type and optional body.
fn build_request(
  method method: http.Method,
  path path: String,
  body body: Option(String),
) -> request.Request(String) {
  let req =
    base_request(method, path)
    |> request.set_header("content-type", "application/json")

  case body {
    Some(json_body) -> request.set_body(req, json_body)
    None -> request.set_body(req, "")
  }
}

/// Builds an HTTP request with multipart/form-data body.
fn build_multipart_request(
  method method: http.Method,
  path path: String,
  form form: List(#(String, FormBody)),
) -> request.Request(BitArray) {
  base_request(method, path)
  |> request.set_header("content-type", "multipart/form-data")
  |> multipart_form.to_request(form)
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
    422 -> {
      // RecordDataValidationError envelope: {"detail": "...", "errors": [...]}.
      // Backwards-compatible: a legacy {"detail": "..."} (plain
      // ValidationError) decodes as Error from field_errors_decoder and
      // falls back to ServerError(422, detail). Empty `errors` list is also
      // treated as the legacy shape — the server-side constructor for
      // RecordDataValidationError forbids zero errors, so a real structured
      // 422 always has at least one entry.
      use body_result <- promise.await(fetch.read_text_body(response))
      case body_result {
        Ok(text_response) -> {
          let err = case
            json.parse(text_response.body, field_errors_decoder())
          {
            Ok(errors) if errors != [] -> types.ValidationError(errors)
            _ -> {
              let detail = case
                json.parse(
                  text_response.body,
                  decode.at(["detail"], decode.string),
                )
              {
                Ok(msg) -> msg
                Error(_) -> "Validation failed"
              }
              types.ServerError(422, detail)
            }
          }
          promise.resolve(Error(err))
        }
        Error(_) ->
          promise.resolve(Error(types.ServerError(422, "Validation failed")))
      }
    }
    code -> {
      use body_result <- promise.await(fetch.read_text_body(response))
      case body_result {
        Ok(text_response) -> {
          let detail = case json.parse(
            text_response.body,
            decode.at(["detail"], decode.string),
          ) {
            Ok(msg) -> msg
            Error(_) -> "Server error"
          }
          // Try to upgrade to StructuredError when the server provides a
          // machine-readable `code` field (and optional `metadata`).
          // Fires for any 4xx with a `code` envelope, not only 409.
          // Falls back to ServerError when `code` is absent (e.g. raw
          // HTTPException(409) in record/auth routers).
          let err = case
            json.parse(text_response.body, structured_payload_decoder())
          {
            Ok(#(error_code, metadata)) ->
              types.StructuredError(error_code, detail, metadata)
            Error(_) -> types.ServerError(code, detail)
          }
          promise.resolve(Error(err))
        }
        Error(_) -> promise.resolve(Error(types.ServerError(code, "Server error")))
      }
    }
  }
}

/// Decodes the optional `{code, metadata}` envelope used by structured
/// 4xx responses (e.g. EntityAlreadyExistsError, RecordLimitReached).
/// Succeeds only when `code` is present; otherwise callers fall back
/// to ServerError.
fn structured_payload_decoder() -> decode.Decoder(
  #(String, Dict(String, String)),
) {
  use error_code <- decode.field("code", decode.string)
  use metadata <- decode.optional_field(
    "metadata",
    dict.new(),
    decode.dict(decode.string, decode.string),
  )
  decode.success(#(error_code, metadata))
}

/// Decodes the `{errors: [{path, message, code}, ...]}` envelope emitted by
/// RecordDataValidationError (422 with structured field-level errors).
/// Succeeds only when the `errors` array is present; otherwise the legacy
/// `{detail: "..."}` shape wins via the fallback in process_response.
fn field_errors_decoder() -> decode.Decoder(List(types.FieldError)) {
  decode.at(
    ["errors"],
    decode.list({
      use path <- decode.field("path", decode.string)
      use message <- decode.field("message", decode.string)
      use code <- decode.field("code", decode.string)
      decode.success(types.FieldError(path, message, code))
    }),
  )
}

/// Makes an HTTP request with optional JSON body.
/// Orchestrates request building, sending, and response processing.
/// Network errors are caught and converted to ApiError types.
pub fn request_with_body(
  method method: http.Method,
  path path: String,
  body body: Option(String),
) -> Promise(Result(Dynamic, ApiError)) {
  let req = build_request(method: method, path: path, body: body)

  use resp_result <- promise.await(fetch.send(req))
  case resp_result {
    Error(fetch.NetworkError(msg)) ->
      promise.resolve(Error(types.NetworkError(msg)))
    Error(_) -> promise.resolve(Error(types.NetworkError("Request failed")))
    Ok(response) -> process_response(response)
  }
}

/// Builds the absolute backend URL for an API path.
///
/// Use for `<a href>` / `<img src>` / form actions where the browser, not
/// `gleam_fetch`, performs the request — e.g. native file downloads via
/// `Content-Disposition: attachment`. For Fetch-driven calls, use the
/// `get`/`post`/... helpers below; they prepend the same prefix internally.
pub fn api_url(path: String) -> String {
  config.base_path() <> "/api" <> path
}

/// Performs a GET request to the specified API path.
pub fn get(path: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(method: http.Get, path: path, body: None)
}

/// Performs a POST request with a JSON body to the specified API path.
pub fn post(path: String, body: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(method: http.Post, path: path, body: Some(body))
}

/// Performs a POST request with multipart/form-data to the specified API path.
/// Takes the multipart body and boundary string for proper content-type header.
pub fn post_multipart(
  path: String,
  form: List(#(String, FormBody)),
) -> Promise(Result(Dynamic, ApiError)) {
  let req = build_multipart_request(method: http.Post, path: path, form: form)

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
  request_with_body(method: http.Patch, path: path, body: Some(body))
}

/// Performs a PUT request with a JSON body to the specified API path.
pub fn put(path: String, body: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(method: http.Put, path: path, body: Some(body))
}

/// Performs a DELETE request to the specified API path.
pub fn delete(path: String) -> Promise(Result(Dynamic, ApiError)) {
  request_with_body(method: http.Delete, path: path, body: None)
}

/// Decodes a Dynamic value using the given decoder, mapping errors to ParseError.
pub fn decode_response(
  data: Dynamic,
  decoder: decode.Decoder(a),
  error_msg: String,
) -> Result(a, ApiError) {
  case decode.run(data, decoder) {
    Ok(value) -> Ok(value)
    Error(_) -> Error(types.ParseError(error_msg))
  }
}
