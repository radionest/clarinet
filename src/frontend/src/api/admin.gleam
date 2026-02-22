// Admin API endpoints
import api/http_client
import api/models.{type AdminStats, type Record}
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
