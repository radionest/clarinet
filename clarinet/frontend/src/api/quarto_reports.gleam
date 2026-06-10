// Quarto reports API endpoints (*.qmd rendered to DOCX/PDF in the background)
import api/http_client
import api/models.{type QuartoReportTemplate, type QuartoRenderState}
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/result
import gleam/uri

const quarto_path = "/admin/quarto-reports"

// List available Quarto report templates
pub fn list_quarto_reports() -> Promise(Result(List(QuartoReportTemplate), ApiError)) {
  http_client.get(quarto_path)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(quarto_report_template_decoder()),
      "Invalid Quarto report templates data",
    ))
  })
}

// Start a background render of `name` in the given formats. The returned
// render state carries the render_id used to poll status and download.
pub fn render_report(
  name: String,
  formats: List(String),
) -> Promise(Result(QuartoRenderState, ApiError)) {
  let body = json.object([#("formats", json.array(formats, json.string))])
  http_client.post(
    quarto_path <> "/" <> uri.percent_encode(name) <> "/render",
    json.to_string(body),
  )
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      quarto_render_state_decoder(),
      "Invalid Quarto render state",
    ))
  })
}

// Poll the status sidecar of a render.
pub fn get_render_status(
  name: String,
  render_id: String,
) -> Promise(Result(QuartoRenderState, ApiError)) {
  http_client.get(
    quarto_path
    <> "/"
    <> uri.percent_encode(name)
    <> "/renders/"
    <> uri.percent_encode(render_id)
    <> "/status",
  )
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      quarto_render_state_decoder(),
      "Invalid Quarto render state",
    ))
  })
}

// Build the URL used as `<a href download>` for the browser to fetch the file.
// Goes directly to the backend — the Content-Disposition header triggers the
// native download flow without an intermediate Fetch call.
pub fn download_url(name: String, render_id: String, format: String) -> String {
  http_client.api_url(
    quarto_path
    <> "/"
    <> uri.percent_encode(name)
    <> "/renders/"
    <> uri.percent_encode(render_id)
    <> "/download?format="
    <> uri.percent_encode(format),
  )
}

fn quarto_report_template_decoder() -> decode.Decoder(QuartoReportTemplate) {
  use name <- decode.field("name", decode.string)
  use title <- decode.field("title", decode.string)
  use description <- decode.field("description", decode.string)
  use data_reports <- decode.field("data_reports", decode.list(decode.string))
  decode.success(models.QuartoReportTemplate(
    name: name,
    title: title,
    description: description,
    data_reports: data_reports,
  ))
}

fn quarto_render_state_decoder() -> decode.Decoder(QuartoRenderState) {
  use render_id <- decode.field("render_id", decode.string)
  use status <- decode.field("status", decode.string)
  use error <- decode.field("error", decode.optional(decode.string))
  decode.success(models.QuartoRenderState(
    render_id: render_id,
    status: status,
    error: error,
  ))
}
