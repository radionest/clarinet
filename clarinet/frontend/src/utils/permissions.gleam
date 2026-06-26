// Record permission helpers
import api/models.{type Record, type User}
import api/types
import gleam/list
import gleam/option.{type Option, None, Some}

/// Check if a user is admin: superuser or member of the built-in 'admin' role.
pub fn is_admin_user(user: User) -> Bool {
  user.is_superuser || list.contains(user.role_names, "admin")
}

/// Check whether a user holds a capability. Admins/superusers implicitly hold
/// every capability (the server includes them too); the `is_admin_user` OR is a
/// belt-and-suspenders guard so nav never vanishes if the field is empty.
pub fn has_capability(user: User, capability: String) -> Bool {
  is_admin_user(user) || list.contains(user.capabilities, capability)
}

/// The capability string for the reports area. Single frontend source of
/// truth; mirrors the backend `Capability.REPORTS`.
pub const reports_capability = "reports"

/// A non-admin user whose only access is the reports capability. Gates the
/// reports-only landing page and trimmed navigation.
pub fn is_reports_only(user: User) -> Bool {
  !is_admin_user(user) && list.contains(user.capabilities, reports_capability)
}

/// Check if user has permission to act on a record
pub fn has_record_permission(user: Option(User), record: Record) -> Bool {
  case user {
    Some(u) ->
      is_admin_user(u)
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

/// Check if user can edit a finished record (Finished + permission + the
/// record type allows post-submit edits — `is_editable` is the server-side
/// verdict on RecordType.editable / edit_window_days; superusers bypass it,
/// mirroring the backend guard)
pub fn can_edit_record(record: Record, user: Option(User)) -> Bool {
  case record.status {
    types.Finished ->
      has_record_permission(user, record)
      && { record.is_editable || is_superuser(user) }
    _ -> False
  }
}

fn is_superuser(user: Option(User)) -> Bool {
  case user {
    Some(u) -> u.is_superuser
    None -> False
  }
}

/// Check if a user can manually fail a record (Pending or InWork + permission)
pub fn can_fail_record(record: Record, user: Option(User)) -> Bool {
  case record.status {
    types.Pending | types.InWork -> has_record_permission(user, record)
    _ -> False
  }
}

/// Check if the current user can delete a record (admin-only cascade).
pub fn can_delete_record(_record: Record, user: Option(User)) -> Bool {
  case user {
    Some(u) -> is_admin_user(u)
    None -> False
  }
}

/// Check if an admin can restart a record (Finished or Failed + auto/slicer + admin)
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
    Some(u) -> is_admin_user(u)
    None -> False
  }
  { is_auto || has_slicer } && is_restartable && is_admin
}
