// Slicer API endpoints for 3D Slicer integration
import api/http_client
import api/types.{type ApiError}
import gleam/dynamic.{type Dynamic}
import gleam/javascript/promise.{type Promise}

/// Open a record's workspace in the user's local 3D Slicer
pub fn open_record(record_id: String) -> Promise(Result(Dynamic, ApiError)) {
  http_client.post("/slicer/records/" <> record_id <> "/open", "{}")
}

/// Run the result validation script for a record in 3D Slicer
pub fn validate_record(record_id: String) -> Promise(Result(Dynamic, ApiError)) {
  http_client.post("/slicer/records/" <> record_id <> "/validate", "{}")
}

/// Clear the current scene in the user's local 3D Slicer
pub fn clear_scene() -> Promise(Result(Dynamic, ApiError)) {
  http_client.post("/slicer/clear", "{}")
}

/// Check if the user's local 3D Slicer instance is reachable
pub fn ping() -> Promise(Result(Dynamic, ApiError)) {
  http_client.get("/slicer/ping")
}
