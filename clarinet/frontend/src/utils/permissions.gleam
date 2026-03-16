// Record permission helpers
import api/models.{type Record, type User}
import api/types
import gleam/option.{type Option, None, Some}

/// Check if user has permission to act on a record
pub fn has_record_permission(user: Option(User), record: Record) -> Bool {
  case user {
    Some(u) ->
      u.is_superuser
      || record.user_id == Some(u.id)
      || record.user_id == option.None
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

/// Check if an admin can restart a record (Finished or Failed + auto/slicer + superuser)
pub fn can_restart_record(record: Record, user: Option(User)) -> Bool {
  let has_slicer = case record.record_type {
    Some(models.RecordType(slicer_script: Some(_), ..)) -> True
    _ -> False
  }
  let is_auto = case record.record_type {
    Some(models.RecordType(role_name: Some("auto"), ..)) -> True
    _ -> False
  }
  let is_restartable = case record.status {
    types.Finished | types.Failed -> True
    _ -> False
  }
  let is_admin = case user {
    Some(u) -> u.is_superuser
    None -> False
  }
  { is_auto || has_slicer } && is_restartable && is_admin
}
