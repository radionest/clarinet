// Project info API endpoint
import api/http_client
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}

pub type ProjectInfo {
  ProjectInfo(project_name: String, project_description: String)
}

fn project_info_decoder() -> decode.Decoder(ProjectInfo) {
  use project_name <- decode.field("project_name", decode.string)
  use project_description <- decode.field("project_description", decode.string)
  decode.success(ProjectInfo(project_name:, project_description:))
}

pub fn get_project_info() -> Promise(Result(ProjectInfo, ApiError)) {
  use data <- promise.map_try(http_client.get("/info"))
  http_client.decode_response(data, project_info_decoder(), "Invalid project info")
}
