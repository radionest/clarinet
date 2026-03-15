// Admin API endpoints
import api/http_client
import api/models.{type AdminStats, type Record, type RecordTypeStats}
import api/records
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/int
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/result

// Get admin dashboard statistics
pub fn get_admin_stats() -> Promise(Result(AdminStats, ApiError)) {
  http_client.get("/admin/stats")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      admin_stats_decoder(),
      "Invalid admin stats data",
    ))
  })
}

// Decoder for AdminStats
pub fn admin_stats_decoder() -> decode.Decoder(AdminStats) {
  use total_studies <- decode.field("total_studies", decode.int)
  use total_records <- decode.field("total_records", decode.int)
  use total_users <- decode.field("total_users", decode.int)
  use total_patients <- decode.field("total_patients", decode.int)
  use records_by_status <- decode.field(
    "records_by_status",
    decode.dict(decode.string, decode.int),
  )

  decode.success(models.AdminStats(
    total_studies: total_studies,
    total_records: total_records,
    total_users: total_users,
    total_patients: total_patients,
    records_by_status: records_by_status,
  ))
}

// Get per-record-type statistics
pub fn get_record_type_stats() -> Promise(Result(List(RecordTypeStats), ApiError)) {
  http_client.get("/admin/record-types/stats")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(record_type_stats_decoder()),
      "Invalid record type stats data",
    ))
  })
}

// Decoder for RecordTypeStatusCounts
fn record_type_status_counts_decoder() -> decode.Decoder(
  models.RecordTypeStatusCounts,
) {
  use blocked <- decode.field("blocked", decode.int)
  use pending <- decode.field("pending", decode.int)
  use inwork <- decode.field("inwork", decode.int)
  use finished <- decode.field("finished", decode.int)
  use failed <- decode.field("failed", decode.int)
  use pause <- decode.field("pause", decode.int)

  decode.success(models.RecordTypeStatusCounts(
    blocked: blocked,
    pending: pending,
    inwork: inwork,
    finished: finished,
    failed: failed,
    pause: pause,
  ))
}

// Decoder for RecordTypeStats
fn record_type_stats_decoder() -> decode.Decoder(models.RecordTypeStats) {
  use name <- decode.field("name", decode.string)
  use description <- decode.field("description", decode.optional(decode.string))
  use label <- decode.field("label", decode.optional(decode.string))
  use level <- decode.field("level", decode.string)
  use role_name <- decode.field("role_name", decode.optional(decode.string))
  use min_records <- decode.field("min_records", decode.optional(decode.int))
  use max_records <- decode.field("max_records", decode.optional(decode.int))
  use total_records <- decode.field("total_records", decode.int)
  use records_by_status <- decode.field(
    "records_by_status",
    record_type_status_counts_decoder(),
  )
  use unique_users <- decode.field("unique_users", decode.int)

  decode.success(models.RecordTypeStats(
    name: name,
    description: description,
    label: label,
    level: level,
    role_name: role_name,
    min_records: min_records,
    max_records: max_records,
    total_records: total_records,
    records_by_status: records_by_status,
    unique_users: unique_users,
  ))
}

// Get role matrix (all roles + users with assignments)
pub fn get_role_matrix() -> Promise(Result(models.RoleMatrix, ApiError)) {
  http_client.get("/admin/role-matrix")
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      role_matrix_decoder(),
      "Invalid role matrix data",
    ))
  })
}

// Add a role to a user
pub fn add_user_role(
  user_id: String,
  role_name: String,
) -> Promise(Result(Nil, ApiError)) {
  http_client.post("/user/" <> user_id <> "/roles/" <> role_name, "{}")
  |> promise.map(fn(res) {
    result.map(res, fn(_) { Nil })
  })
}

// Remove a role from a user
pub fn remove_user_role(
  user_id: String,
  role_name: String,
) -> Promise(Result(Nil, ApiError)) {
  http_client.delete("/user/" <> user_id <> "/roles/" <> role_name)
  |> promise.map(fn(res) {
    result.map(res, fn(_) { Nil })
  })
}

// Decoder for UserRoleInfo
fn user_role_info_decoder() -> decode.Decoder(models.UserRoleInfo) {
  use id <- decode.field("id", decode.string)
  use email <- decode.field("email", decode.string)
  use is_active <- decode.field("is_active", decode.bool)
  use is_superuser <- decode.field("is_superuser", decode.bool)
  use role_names <- decode.field("role_names", decode.list(decode.string))

  decode.success(models.UserRoleInfo(
    id: id,
    email: email,
    is_active: is_active,
    is_superuser: is_superuser,
    role_names: role_names,
  ))
}

// Decoder for RoleMatrix
fn role_matrix_decoder() -> decode.Decoder(models.RoleMatrix) {
  use roles <- decode.field("roles", decode.list(decode.string))
  use users <- decode.field("users", decode.list(user_role_info_decoder()))

  decode.success(models.RoleMatrix(roles: roles, users: users))
}

// Assign a user to a record (superuser only)
pub fn assign_record_user(
  record_id: Int,
  user_id: String,
) -> Promise(Result(Record, ApiError)) {
  let path =
    "/admin/records/"
    <> int.to_string(record_id)
    <> "/assign?user_id="
    <> user_id
  http_client.patch(path, json.to_string(json.object([])))
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      records.record_decoder(),
      "Invalid record data",
    ))
  })
}

// Update record status (superuser only)
pub fn update_record_status(
  record_id: Int,
  status: String,
) -> Promise(Result(Record, ApiError)) {
  let path =
    "/admin/records/"
    <> int.to_string(record_id)
    <> "/status?record_status="
    <> status
  http_client.patch(path, json.to_string(json.object([])))
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      records.record_decoder(),
      "Invalid record data",
    ))
  })
}

// Unassign user from a record (superuser only)
pub fn unassign_record_user(
  record_id: Int,
) -> Promise(Result(Record, ApiError)) {
  let path = "/admin/records/" <> int.to_string(record_id) <> "/user"
  http_client.delete(path)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      records.record_decoder(),
      "Invalid record data",
    ))
  })
}
