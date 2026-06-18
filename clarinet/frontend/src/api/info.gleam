// Project info API endpoint
import api/http_client
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/option.{type Option, None}

pub type ViewerInfo {
  ViewerInfo(name: String, pacs_name: Option(String))
}

pub type ProjectInfo {
  ProjectInfo(
    project_name: String,
    project_description: String,
    viewers: List(ViewerInfo),
    sse_enabled: Bool,
    // Deployment uses per-study anonymized patient IDs. When True the simple
    // per-patient anon_id must not be shown (it is stable across studies and
    // would defeat per-study unlinkability).
    anon_per_study: Bool,
  )
}

fn viewer_info_decoder() -> decode.Decoder(ViewerInfo) {
  use name <- decode.field("name", decode.string)
  use pacs_name <- decode.optional_field("pacs_name", None, decode.optional(decode.string))
  decode.success(ViewerInfo(name:, pacs_name:))
}

fn project_info_decoder() -> decode.Decoder(ProjectInfo) {
  use project_name <- decode.field("project_name", decode.string)
  use project_description <- decode.field("project_description", decode.string)
  use viewers <- decode.optional_field("viewers", [], decode.list(viewer_info_decoder()))
  use sse_enabled <- decode.optional_field("sse_enabled", False, decode.bool)
  use anon_per_study <- decode.optional_field(
    "anon_per_study_patient_id",
    False,
    decode.bool,
  )
  decode.success(ProjectInfo(
    project_name:,
    project_description:,
    viewers:,
    sse_enabled:,
    anon_per_study:,
  ))
}

pub fn get_project_info() -> Promise(Result(ProjectInfo, ApiError)) {
  use data <- promise.map_try(http_client.get("/info"))
  http_client.decode_response(data, project_info_decoder(), "Invalid project info")
}
