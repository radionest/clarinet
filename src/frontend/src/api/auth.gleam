// Authentication API endpoints
import api/http_client
import api/models.{type LoginResponse, type RegisterRequest, type User}
import api/types.{type ApiError}
import gleam/dynamic
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/option.{type Option, None, Some}
import gleam/result
import multipart_form/field

// Login endpoint using multipart/form-data
pub fn login(
  username: String,
  password: String,
) -> Promise(Result(LoginResponse, ApiError)) {
  // Create a multipart form with username and password fields
  let form = [
    #("username", field.String(username)),
    #("password", field.String(password)),
  ]
  // Send the multipart form data
  http_client.post_multipart("/auth/login", form)
  |> promise.map(fn(result) { result.try(result, decode_login_response) })
}

// Logout endpoint
pub fn logout() -> Promise(Result(Nil, ApiError)) {
  let body =
    json.object([])
    |> json.to_string

  http_client.post("/auth/logout", body)
  |> promise.map(fn(result) { result.map(result, fn(_) { Nil }) })
}

// Get current user
pub fn get_current_user() -> Promise(Result(User, ApiError)) {
  http_client.get("/auth/me")
  |> promise.map(fn(result) { result.try(result, decode_user) })
}

// Register new user
pub fn register(request: RegisterRequest) -> Promise(Result(User, ApiError)) {
  let body =
    json.object([
      #("username", json.string(request.username)),
      #("email", json.string(request.email)),
      #("password", json.string(request.password)),
      #("full_name", case request.full_name {
        Some(name) -> json.string(name)
        None -> json.null()
      }),
    ])
    |> json.to_string

  http_client.post("/auth/register", body)
  |> promise.map(fn(result) { result.try(result, decode_user) })
}

// Refresh token
pub fn refresh_token() -> Promise(Result(LoginResponse, ApiError)) {
  let body =
    json.object([])
    |> json.to_string

  http_client.post("/auth/refresh", body)
  |> promise.map(fn(result) { result.try(result, decode_login_response) })
}

// Decode login response from dynamic data (cookie auth - no token in response)
fn decode_login_response(
  data: dynamic.Dynamic,
) -> Result(LoginResponse, ApiError) {
  // Login now returns just the user data
  case decode_user(data) {
    Ok(user) -> Ok(models.LoginResponse(user: user))
    Error(e) -> Error(e)
  }
}

// Decode user from dynamic data
fn decode_user(data: dynamic.Dynamic) -> Result(User, ApiError) {
  let decoder = {
    use id <- decode.field("id", decode.string)
    use username <- decode.field("username", decode.string)
    use email <- decode.field("email", decode.string)
    use hashed_password <- decode.optional_field(
      "hashed_password",
      None,
      decode.optional(decode.string),
    )
    use is_active <- decode.optional_field("is_active", True, decode.bool)
    use is_superuser <- decode.optional_field(
      "is_superuser",
      False,
      decode.bool,
    )
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
