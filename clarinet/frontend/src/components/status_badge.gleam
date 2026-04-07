// Reusable colored status badge for RecordStatus
import api/types
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html

/// Render a colored badge for the given record status.
pub fn render(status: types.RecordStatus) -> Element(msg) {
  let #(class_suffix, label) = case status {
    types.Blocked -> #("badge-blocked", "Blocked")
    types.Pending -> #("badge-pending", "Pending")
    types.InWork -> #("badge-progress", "In Progress")
    types.Finished -> #("badge-success", "Completed")
    types.Failed -> #("badge-danger", "Failed")
    types.Paused -> #("badge-paused", "Paused")
  }
  html.span([attribute.class("badge " <> class_suffix)], [html.text(label)])
}
