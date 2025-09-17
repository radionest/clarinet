// HTTP client for API communication
import gleam/http
import gleam/http/request.{type Request}
import gleam/http/response.{type Response}
import gleam/json
import gleam/dynamic
import gleam/dynamic/decode
import gleam/option.{type Option, None, Some}
import gleam/list
import gleam/string
import gleam/javascript/promise.{type Promise}
import api/types.{type ApiConfig, type ApiError}

// Create a new API client configuration
pub fn create_client(base_url: String) -> ApiConfig {
  types.ApiConfig(base_url: base_url, token: None)
}

// Set authentication token
pub fn with_token(config: ApiConfig, token: String) -> ApiConfig {
  types.ApiConfig(..config, token: Some(token))
}

// Build request with authentication headers
fn build_request(
  config: ApiConfig,
  method: http.Method,
  path: String,
  body: Option(String),
) -> Request(String) {
  let url = config.base_url <> "/api" <> path

  let req = request.new()
    |> request.set_method(method)
    |> request.set_host(get_host(config.base_url))
    |> request.set_path("/api" <> path)
    |> request.prepend_header("content-type", "application/json")
    |> request.prepend_header("accept", "application/json")

  // Add auth header if token exists
  let req = case config.token {
    Some(token) -> request.prepend_header(req, "authorization", "Bearer " <> token)
    None -> req
  }

  // Add body if present
  case body {
    Some(json_body) -> request.set_body(req, json_body)
    None -> req
  }
}

// Extract host from URL
fn get_host(url: String) -> String {
  url
  |> string.replace("http://", "")
  |> string.replace("https://", "")
  |> string.split("/")
  |> fn(parts) {
    case parts {
      [host, ..] -> host
      [] -> "localhost"
    }
  }
}

// Handle API response
fn handle_response(response: Response(String)) -> Result(dynamic.Dynamic, ApiError) {
  case response.status {
    200 | 201 | 204 -> {
      // Parse JSON string to dynamic
      case json.parse(response.body, decode.dynamic) {
        Ok(data) -> Ok(data)
        Error(_) -> Error(types.ParseError("Failed to parse response"))
      }
    }
    401 -> Error(types.AuthError("Unauthorized"))
    400 -> {
      // Try to parse validation errors
      case json.parse(response.body, decode.dynamic) {
        Ok(_) -> Error(types.ValidationError([]))
        Error(_) -> Error(types.ServerError(400, "Bad Request"))
      }
    }
    code -> Error(types.ServerError(code, "Server error"))
  }
}

// GET request
pub fn get(
  config: ApiConfig,
  path: String,
) -> Promise(Result(dynamic.Dynamic, ApiError)) {
  let req = build_request(config, http.Get, path, None)
  fetch_request(req)
}

// POST request
pub fn post(
  config: ApiConfig,
  path: String,
  body: String,
) -> Promise(Result(dynamic.Dynamic, ApiError)) {
  let req = build_request(config, http.Post, path, Some(body))
  fetch_request(req)
}

// PUT request
pub fn put(
  config: ApiConfig,
  path: String,
  body: String,
) -> Promise(Result(dynamic.Dynamic, ApiError)) {
  let req = build_request(config, http.Put, path, Some(body))
  fetch_request(req)
}

// DELETE request
pub fn delete(
  config: ApiConfig,
  path: String,
) -> Promise(Result(dynamic.Dynamic, ApiError)) {
  let req = build_request(config, http.Delete, path, None)
  fetch_request(req)
}

// External JavaScript function for making HTTP requests
@external(javascript, "../ffi/http.js", "fetchRequest")
fn fetch_request(request: Request(String)) -> Promise(Result(dynamic.Dynamic, ApiError))

// Helper to build query parameters
pub fn with_query_params(path: String, params: List(#(String, String))) -> String {
  case params {
    [] -> path
    params -> {
      let query = params
        |> list.map(fn(pair) {
          let #(key, value) = pair
          key <> "=" <> url_encode(value)
        })
        |> string.join("&")
      path <> "?" <> query
    }
  }
}

// Simple URL encoding (basic implementation)
fn url_encode(str: String) -> String {
  str
  |> string.replace(" ", "%20")
  |> string.replace("&", "%26")
  |> string.replace("=", "%3D")
}