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
  use pending <- decode.field("pending", decode.int)
  use inwork <- decode.field("inwork", decode.int)
  use finished <- decode.field("finished", decode.int)
  use failed <- decode.field("failed", decode.int)
  use pause <- decode.field("pause", decode.int)

  decode.success(models.RecordTypeStatusCounts(
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
  use min_users <- decode.field("min_users", decode.optional(decode.int))
  use max_users <- decode.field("max_users", decode.optional(decode.int))
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
    min_users: min_users,
    max_users: max_users,
    total_records: total_records,
    records_by_status: records_by_status,
    unique_users: unique_users,
  ))
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
