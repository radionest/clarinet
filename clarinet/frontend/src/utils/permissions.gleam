// Record permission helpers
import api/models.{type Record, type User}
import api/types
import gleam/option.{type Option, Some}

/// Check if user has permission to act on a record
pub fn has_record_permission(user: Option(User), record: Record) -> Bool {
  case user {
    Some(u) -> u.is_superuser || record.user_id == Some(u.id)
    _ -> False
  }
}

/// Check if user can fill a record (Pending or InWork + permission)
pub fn can_fill_record(record: Record, user: Option(User)) -> Bool {
  case record.status {
    types.Pending | types.InWork -> has_record_permission(user, record)
    _ -> False
  }
}

/// Check if user can edit a finished record (Finished + permission)
pub fn can_edit_record(record: Record, user: Option(User)) -> Bool {
  case record.status {
    types.Finished -> has_record_permission(user, record)
    _ -> False
  }
}
