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

/// Whether the server lets the current viewer run `command` on this record —
/// `RecordRead.allowed_commands`, computed per request by the lifecycle policy
/// (type role, ownership / shared editing, the edit lock, admin rights).
pub fn allows(record: Record, command: String) -> Bool {
  list.contains(record.allowed_commands, command)
}

/// Check if the viewer can fill a record (Pending or InWork, and the server allows submit)
pub fn can_fill_record(record: Record, _user: Option(User)) -> Bool {
  case record.status {
    types.Pending | types.InWork -> allows(record, "submit")
    _ -> False
  }
}

/// Check if the viewer can edit a finished record's data
pub fn can_edit_record(record: Record, _user: Option(User)) -> Bool {
  allows(record, "edit")
}

/// Check if the viewer can manually fail a record
pub fn can_fail_record(record: Record, _user: Option(User)) -> Bool {
  allows(record, "fail")
}

/// Check if the current user can delete a record (admin-only cascade).
pub fn can_delete_record(_record: Record, user: Option(User)) -> Bool {
  case user {
    Some(u) -> is_admin_user(u)
    None -> False
  }
}

/// Check if an admin can restart a record (Finished or Failed + auto/slicer +
/// admin, and the server allows restart)
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
  { is_auto || has_slicer }
  && is_restartable
  && is_admin
  && allows(record, "restart")
}

/// Whether the viewer may give the record back — its owner on a `releasable`
/// type, or an admin
pub fn can_release_record(record: Record) -> Bool {
  allows(record, "unassign")
}
