// Authentication API endpoints
import gleam/json
import gleam/dynamic
import gleam/dynamic/decode
import gleam/result
import gleam/option.{type Option, None, Some}
import gleam/javascript/promise.{type Promise}
import api/client
import api/types.{type ApiConfig, type ApiError}
import api/models.{type LoginResponse, type RegisterRequest, type User}

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
  |> json.to_string

  client.post(config, "/auth/login", body)
  |> promise.map(fn(result) {
    result.try(result, decode_login_response)
  })
}

// Logout endpoint
pub fn logout(config: ApiConfig) -> Promise(Result(Nil, ApiError)) {
  let body = json.object([])
  |> json.to_string

  client.post(config, "/auth/logout", body)
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
  |> json.to_string

  client.post(config, "/auth/register", body)
  |> promise.map(fn(result) {
    result.try(result, decode_user)
  })
}

// Refresh token
pub fn refresh_token(config: ApiConfig) -> Promise(Result(LoginResponse, ApiError)) {
  let body = json.object([])
  |> json.to_string

  client.post(config, "/auth/refresh", body)
  |> promise.map(fn(result) {
    result.try(result, decode_login_response)
  })
}

// Decode login response from dynamic data
fn decode_login_response(data: dynamic.Dynamic) -> Result(LoginResponse, ApiError) {
  let decoder = {
    use access_token <- decode.field("access_token", decode.string)
    use token_type <- decode.optional_field("token_type", "Bearer", decode.string)
    use user_data <- decode.field("user", decode.dynamic)

    // Decode the nested user
    case decode_user(user_data) {
      Ok(user) ->
        decode.success(models.LoginResponse(
          access_token: access_token,
          token_type: token_type,
          user: user,
        ))
      Error(_e) -> decode.failure(
        models.LoginResponse(access_token: "", token_type: "", user: models.User(
          id: "",
          username: "",
          email: "",
          hashed_password: None,
          is_active: False,
          is_superuser: False,
          is_verified: False,
          roles: None,
          tasks: None,
        )),
        "Failed to decode user"
      )
    }
  }

  case decode.run(data, decoder) {
    Ok(response) -> Ok(response)
    Error(_) -> Error(types.ParseError("Invalid login response"))
  }
}

// Decode user from dynamic data
fn decode_user(data: dynamic.Dynamic) -> Result(User, ApiError) {
  let decoder = {
    use id <- decode.field("id", decode.string)
    use username <- decode.field("username", decode.string)
    use email <- decode.field("email", decode.string)
    use hashed_password <- decode.optional_field("hashed_password", None, decode.optional(decode.string))
    use is_active <- decode.optional_field("is_active", True, decode.bool)
    use is_superuser <- decode.optional_field("is_superuser", False, decode.bool)
    use is_verified <- decode.optional_field("is_verified", False, decode.bool)

    // For now, skip decoding complex nested types
    let roles = None
    let tasks = None

    decode.success(models.User(
      id: id,
      username: username,
      email: email,
      hashed_password: hashed_password,
      is_active: is_active,
      is_superuser: is_superuser,
      is_verified: is_verified,
      roles: roles,
      tasks: tasks,
    ))
  }

  case decode.run(data, decoder) {
    Ok(user) -> Ok(user)
    Error(_) -> Error(types.ParseError("Invalid user data"))
  }
}

// Store token in localStorage (calls JavaScript FFI)
@external(javascript, "../ffi/http.js", "storeToken")
pub fn store_token(token: String) -> Nil

// Get stored token from localStorage
@external(javascript, "../ffi/http.js", "getStoredToken")
pub fn get_stored_token() -> Option(String)

// Clear token from localStorage
@external(javascript, "../ffi/http.js", "clearToken")
pub fn clear_token() -> Nil