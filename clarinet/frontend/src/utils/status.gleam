// Shared status conversion utilities for RecordStatus
import api/types
import clarinet_frontend/i18n

pub fn all_statuses() -> List(types.RecordStatus) {
  [types.Blocked, types.Pending, types.InWork, types.Finished, types.Failed, types.Paused]
}

pub fn to_i18n_key(status: types.RecordStatus) -> i18n.Key {
  case status {
    types.Blocked -> i18n.StatusBlocked
    types.Pending -> i18n.StatusPending
    types.InWork -> i18n.StatusInProgress
    types.Finished -> i18n.StatusCompleted
    types.Failed -> i18n.StatusFailed
    types.Paused -> i18n.StatusPaused
  }
}

/// Human-readable display text for a RecordStatus
pub fn display_text(status: types.RecordStatus) -> String {
  case status {
    types.Blocked -> "Blocked"
    types.Pending -> "Pending"
    types.InWork -> "In Progress"
    types.Finished -> "Completed"
    types.Failed -> "Failed"
    types.Paused -> "Paused"
  }
}

/// Convert RecordStatus to its backend string representation
pub fn to_backend_string(status: types.RecordStatus) -> String {
  case status {
    types.Blocked -> "blocked"
    types.Pending -> "pending"
    types.InWork -> "inwork"
    types.Finished -> "finished"
    types.Failed -> "failed"
    types.Paused -> "paused"
  }
}

/// Parse a backend status string into a RecordStatus (defaults to Pending).
/// Accepts both `"paused"` (round-trips with `to_backend_string`) and the
/// historical `"pause"` (older AdminStats payloads still ship this).
pub fn from_backend_string(s: String) -> types.RecordStatus {
  case s {
    "blocked" -> types.Blocked
    "pending" -> types.Pending
    "inwork" -> types.InWork
    "finished" -> types.Finished
    "failed" -> types.Failed
    "paused" | "pause" -> types.Paused
    _ -> types.Pending
  }
}

/// Tailwind colour name for a status badge. Pair with
/// `status.to_i18n_key(s) |> translate` for the label. Exhaustive match —
/// adding a new `RecordStatus` variant is a compile error here.
pub fn color(status: types.RecordStatus) -> String {
  case status {
    types.Blocked -> "yellow"
    types.Pending -> "blue"
    types.InWork -> "orange"
    types.Finished -> "green"
    types.Failed -> "red"
    types.Paused -> "gray"
  }
}
