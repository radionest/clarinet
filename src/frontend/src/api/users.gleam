// User API endpoints
import api/http_client
import api/models.{type User}
import api/types.{type ApiError}
import gleam/dynamic
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/result

// Get all users
pub fn get_users() -> Promise(Result(List(User), ApiError)) {
  http_client.get("/user/")
  |> promise.map(fn(res) { result.try(res, decode_users) })
}

// Public decoder for reuse
pub fn user_decoder() -> decode.Decoder(User) {
  use id <- decode.field("id", decode.string)
  use email <- decode.field("email", decode.string)
  use is_active <- decode.optional_field("is_active", True, decode.bool)
  use is_superuser <- decode.optional_field("is_superuser", False, decode.bool)
  use is_verified <- decode.optional_field("is_verified", False, decode.bool)

  decode.success(models.User(
    id: id,
    email: email,
    is_active: is_active,
    is_superuser: is_superuser,
    is_verified: is_verified,
  ))
}

fn decode_users(data: dynamic.Dynamic) -> Result(List(User), ApiError) {
  case decode.run(data, decode.list(user_decoder())) {
    Ok(users) -> Ok(users)
    Error(_) -> Error(types.ParseError("Invalid users data"))
  }
}
