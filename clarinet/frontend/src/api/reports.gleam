// Custom SQL reports API endpoints
import api/http_client
import api/models.{type ReportTemplate}
import api/types.{type ApiError}
import gleam/dynamic/decode
import gleam/javascript/promise.{type Promise}
import gleam/result

const reports_path = "/admin/reports"

// List available SQL report templates
pub fn list_reports() -> Promise(Result(List(ReportTemplate), ApiError)) {
  http_client.get(reports_path)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      decode.list(report_template_decoder()),
      "Invalid report templates data",
    ))
  })
}

// Build the URL used as `<a href download>` for the browser to fetch the file.
// Goes directly to the backend — the response's Content-Disposition header
// triggers the native download flow without an intermediate Fetch call.
pub fn download_url(name: String, format: String) -> String {
  http_client.api_url(reports_path <> "/" <> name <> "/download?format=" <> format)
}

fn report_template_decoder() -> decode.Decoder(ReportTemplate) {
  use name <- decode.field("name", decode.string)
  use title <- decode.field("title", decode.string)
  use description <- decode.field("description", decode.string)
  decode.success(models.ReportTemplate(
    name: name,
    title: title,
    description: description,
  ))
}
