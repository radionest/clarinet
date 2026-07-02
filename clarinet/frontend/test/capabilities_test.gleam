import api/models
import gleam/option.{None, Some}
import gleeunit/should
import router
import utils/permissions

fn make_user(
  role_names: List(String),
  capabilities: List(String),
  is_superuser: Bool,
) -> models.User {
  models.User(
    id: "u1",
    email: "u@test",
    is_active: True,
    is_superuser: is_superuser,
    is_verified: True,
    role_names: role_names,
    capabilities: capabilities,
  )
}

pub fn has_capability_true_when_listed_test() {
  make_user([], ["reports"], False)
  |> permissions.has_capability("reports")
  |> should.equal(True)
}

pub fn has_capability_true_for_admin_test() {
  make_user(["admin"], [], False)
  |> permissions.has_capability("reports")
  |> should.equal(True)
}

pub fn has_capability_false_otherwise_test() {
  make_user(["doctor"], [], False)
  |> permissions.has_capability("reports")
  |> should.equal(False)
}

pub fn reports_route_requires_reports_capability_test() {
  router.requires_capability(router.AdminReports)
  |> should.equal(Some("reports"))
}

pub fn reports_route_not_admin_gated_test() {
  router.requires_admin_role(router.AdminReports)
  |> should.equal(False)
}

pub fn workflow_route_has_no_capability_test() {
  router.requires_capability(router.AdminWorkflow)
  |> should.equal(None)
}
