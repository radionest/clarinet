// Timestamp formatting for audit/activity display.
import gleam/string

/// Format a backend ISO-8601 timestamp as "YYYY-MM-DD HH:MM" for compact
/// display. Drops seconds, fractional seconds and timezone — audit feeds need
/// only minute granularity. Returns the input unchanged when it is too short
/// to contain the expected prefix.
pub fn format(iso: String) -> String {
  case string.length(iso) >= 16 {
    True -> string.slice(iso, 0, 10) <> " " <> string.slice(iso, 11, 5)
    False -> iso
  }
}
