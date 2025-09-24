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
// Returns 204 No Content on success - must call get_current_user to get user data
pub fn login(
  email: String,
  password: String,
) -> Promise(Result(LoginResponse, ApiError)) {
  // Create a multipart form with username field (but email value - fastapi-users convention)
  // The backend expects field named "username" but containing email value
  let form = [
    #("username", field.String(email)),
    #("password", field.String(password)),
  ]

  // Send the login request - it returns 204 No Content on success
  // Using try_await to handle the Result inside the promise
  use _login <- promise.try_await(http_client.post_multipart("/auth/login", form))
  use current_user <- promise.map_try(get_current_user())
  Ok(models.LoginResponse(user: current_user))
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
      #("email", json.string(request.email)),
      #("password", json.string(request.password)),
      #("full_name", case request.full_name {
        Some(name) -> json.string(name)
        None -> json.null()
      }),
    ])
    |> json.to_string

  http_client.post("/api/auth/register", body)
  |> promise.map(fn(result) { result.try(result, decode_user) })
}

// Refresh token
pub fn refresh_token() -> Promise(Result(LoginResponse, ApiError)) {
  let body =
    json.object([])
    |> json.to_string

  // Refresh also returns 204, so chain get_current_user
  // Using try_await to handle the Result and reduce nesting
  http_client.post("/auth/refresh", body)
  |> promise.try_await(fn(_) {
    // Refresh successful - fetch current user
    get_current_user()
  })
  |> promise.map(fn(result) {
    // Transform the user result into a LoginResponse
    result.map(result, fn(user) { models.LoginResponse(user: user) })
  })
}


// Decode user from dynamic data
fn decode_user(data: dynamic.Dynamic) -> Result(User, ApiError) {
  let decoder = {
    use id <- decode.field("id", decode.string)
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
