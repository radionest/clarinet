// Reusable colored status badge for RecordStatus
import api/types
import clarinet_frontend/i18n.{type Key}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html

/// Render a colored badge for the given record status.
pub fn render(
  status: types.RecordStatus,
  translate: fn(Key) -> String,
) -> Element(msg) {
  let #(class_suffix, key) = case status {
    types.Blocked -> #("badge-blocked", i18n.StatusBlocked)
    types.Pending -> #("badge-pending", i18n.StatusPending)
    types.InWork -> #("badge-progress", i18n.StatusInProgress)
    types.Finished -> #("badge-success", i18n.StatusCompleted)
    types.Failed -> #("badge-danger", i18n.StatusFailed)
    types.Paused -> #("badge-paused", i18n.StatusPaused)
  }
  html.span([attribute.class("badge " <> class_suffix)], [html.text(translate(key))])
}
