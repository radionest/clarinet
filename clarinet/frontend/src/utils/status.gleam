// Shared status conversion utilities for RecordStatus
import api/types

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

/// Parse a backend status string into a RecordStatus (defaults to Pending)
pub fn from_backend_string(s: String) -> types.RecordStatus {
  case s {
    "blocked" -> types.Blocked
    "pending" -> types.Pending
    "inwork" -> types.InWork
    "finished" -> types.Finished
    "failed" -> types.Failed
    "pause" -> types.Paused
    _ -> types.Pending
  }
}
