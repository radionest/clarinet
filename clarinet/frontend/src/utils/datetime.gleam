// Timestamp formatting for audit/activity display.
import gleam/string

/// Format a backend ISO-8601 timestamp as "YYYY-MM-DD HH:MM UTC" for compact
/// display. Drops seconds and fractional seconds (audit feeds need only minute
/// granularity); backend timestamps are UTC, so the suffix labels the zone
/// explicitly rather than silently dropping it. Returns the input unchanged
/// when it is too short to contain the expected prefix.
pub fn format(iso: String) -> String {
  case string.length(iso) >= 16 {
    True -> string.slice(iso, 0, 10) <> " " <> string.slice(iso, 11, 5) <> " UTC"
    False -> iso
  }
}
