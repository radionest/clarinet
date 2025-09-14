// Authentication API endpoints
import gleam/json
import gleam/result
import gleam/javascript/promise.{type Promise}
import api/client
import api/types.{type ApiConfig, type ApiError}
import api/models.{type LoginRequest, type LoginResponse, type RegisterRequest, type User}

// Login endpoint
pub fn login(
  config: ApiConfig,
  username: String,
  password: String,
) -> Promise(Result(LoginResponse, ApiError)) {
  let body = json.object([
    #("username", json.string(username)),
    #("password", json.string(password)),
  ])

  client.post(config, "/auth/login", body)
  |> promise.map(fn(result) {
    result.try(result, decode_login_response)
  })
}

// Logout endpoint
pub fn logout(config: ApiConfig) -> Promise(Result(Nil, ApiError)) {
  client.post(config, "/auth/logout", json.object([]))
  |> promise.map(fn(result) {
    result.map(result, fn(_) { Nil })
  })
}

// Get current user
pub fn get_current_user(config: ApiConfig) -> Promise(Result(User, ApiError)) {
  client.get(config, "/auth/me")
  |> promise.map(fn(result) {
    result.try(result, decode_user)
  })
}

// Register new user
pub fn register(
  config: ApiConfig,
  request: RegisterRequest,
) -> Promise(Result(User, ApiError)) {
  let body = json.object([
    #("username", json.string(request.username)),
    #("email", json.string(request.email)),
    #("password", json.string(request.password)),
    #("full_name", case request.full_name {
      Some(name) -> json.string(name)
      None -> json.null()
    }),
  ])

  client.post(config, "/auth/register", body)
  |> promise.map(fn(result) {
    result.try(result, decode_user)
  })
}

// Refresh token
pub fn refresh_token(config: ApiConfig) -> Promise(Result(LoginResponse, ApiError)) {
  client.post(config, "/auth/refresh", json.object([]))
  |> promise.map(fn(result) {
    result.try(result, decode_login_response)
  })
}

// Decode login response from JSON
fn decode_login_response(data: json.Json) -> Result(LoginResponse, ApiError) {
  // This is a simplified decoder - in production, use proper JSON decoding
  case data {
    json.Object(obj) -> {
      case dict.get(obj, "access_token"), dict.get(obj, "user") {
        Ok(json.String(token)), Ok(user_json) -> {
          case decode_user(user_json) {
            Ok(user) -> Ok(LoginResponse(
              access_token: token,
              token_type: "Bearer",
              user: user,
            ))
            Error(e) -> Error(e)
          }
        }
        _, _ -> Error(types.ParseError("Invalid login response"))
      }
    }
    _ -> Error(types.ParseError("Invalid login response"))
  }
}

// Decode user from JSON
fn decode_user(data: json.Json) -> Result(User, ApiError) {
  // Simplified decoder - replace with proper JSON decoding
  case data {
    json.Object(obj) -> {
      case dict.get(obj, "id"), dict.get(obj, "username"), dict.get(obj, "email") {
        Ok(json.Number(id)), Ok(json.String(username)), Ok(json.String(email)) -> {
          Ok(User(
            id: float.round(id),
            username: username,
            email: email,
            full_name: get_optional_string(obj, "full_name"),
            role: decode_role(dict.get(obj, "role")),
            is_active: get_bool(obj, "is_active", True),
            created_at: get_optional_string(obj, "created_at"),
            last_login: get_optional_string(obj, "last_login"),
          ))
        }
        _, _, _ -> Error(types.ParseError("Invalid user data"))
      }
    }
    _ -> Error(types.ParseError("Invalid user data"))
  }
}

// Helper to decode role
fn decode_role(role_json: Result(json.Json, _)) -> models.UserRole {
  case role_json {
    Ok(json.String("admin")) -> models.Admin
    Ok(json.String("viewer")) -> models.Viewer
    _ -> models.User
  }
}

// Helper to get optional string from JSON object
fn get_optional_string(obj: dict.Dict(String, json.Json), key: String) -> option.Option(String) {
  case dict.get(obj, key) {
    Ok(json.String(s)) -> option.Some(s)
    _ -> option.None
  }
}

// Helper to get boolean from JSON object with default
fn get_bool(obj: dict.Dict(String, json.Json), key: String, default: Bool) -> Bool {
  case dict.get(obj, key) {
    Ok(json.Boolean(b)) -> b
    _ -> default
  }
}

// Store token in localStorage (calls JavaScript FFI)
@external(javascript, "../ffi/http.js", "storeToken")
pub fn store_token(token: String) -> Nil

// Get stored token from localStorage
@external(javascript, "../ffi/http.js", "getStoredToken")
pub fn get_stored_token() -> option.Option(String)

// Clear token from localStorage
@external(javascript, "../ffi/http.js", "clearToken")
pub fn clear_token() -> Nil