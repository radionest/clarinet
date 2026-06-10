// Unit tests for previous_route tracking in main.update's OnRouteChange
// handler: saved on ordinary transitions, kept on same-route echoes and
// record→record hops, never set to auth pages. Effects are not executed —
// only the resulting store.Model is asserted.
import api/models
import gleam/option.{None, Some}
import gleeunit/should
import main
import router
import store

fn make_user() -> models.User {
  models.User(
    id: "u1",
    email: "u@example.com",
    is_active: True,
    is_superuser: False,
    is_verified: True,
    role_names: [],
  )
}

fn make_model(route: router.Route) -> store.Model {
  store.Model(
    ..store.init(),
    user: Some(make_user()),
    checking_session: False,
    route: route,
  )
}

pub fn route_change_saves_previous_route_test() {
  let model = make_model(router.PatientDetail("p1"))
  let #(new_model, _eff) =
    main.update(model, store.OnRouteChange(router.RecordDetail("5")))
  new_model.previous_route |> should.equal(Some(router.PatientDetail("p1")))
}

pub fn record_to_record_keeps_previous_route_test() {
  let model =
    store.Model(
      ..make_model(router.RecordDetail("5")),
      previous_route: Some(router.PatientDetail("p1")),
    )
  let #(new_model, _eff) =
    main.update(model, store.OnRouteChange(router.RecordDetail("6")))
  new_model.previous_route |> should.equal(Some(router.PatientDetail("p1")))
}

pub fn same_route_echo_keeps_previous_route_test() {
  let model =
    store.Model(
      ..make_model(router.RecordDetail("5")),
      previous_route: Some(router.PatientDetail("p1")),
    )
  let #(new_model, _eff) =
    main.update(model, store.OnRouteChange(router.RecordDetail("5")))
  new_model.previous_route |> should.equal(Some(router.PatientDetail("p1")))
}

pub fn login_route_is_not_saved_test() {
  let model = make_model(router.Login)
  let #(new_model, _eff) = main.update(model, store.OnRouteChange(router.Home))
  new_model.previous_route |> should.equal(None)
}
