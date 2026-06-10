// Unit tests for previous_route tracking in main.update's OnRouteChange
// handler: saved on ordinary transitions (with list filters stripped), kept
// on same-route echoes and record→record hops, never set to auth/404 pages
// or the create-record form. Effects are not executed — only the resulting
// store.Model is asserted.
import api/models
import gleam/dict
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

pub fn register_route_is_not_saved_test() {
  let model = make_model(router.Register)
  let #(new_model, _eff) = main.update(model, store.OnRouteChange(router.Home))
  new_model.previous_route |> should.equal(None)
}

pub fn not_found_route_is_not_saved_test() {
  let model = make_model(router.NotFound)
  let #(new_model, _eff) = main.update(model, store.OnRouteChange(router.Home))
  new_model.previous_route |> should.equal(None)
}

pub fn record_new_route_is_not_saved_test() {
  let model = make_model(router.RecordNew)
  let #(new_model, _eff) =
    main.update(model, store.OnRouteChange(router.RecordDetail("5")))
  new_model.previous_route |> should.equal(None)
}

pub fn list_filters_are_stripped_test() {
  // Filters in model.route go stale (lists sync them via replace_state),
  // so the saved previous_route must carry an empty filter dict.
  let filters = dict.from_list([#("status", "pending")])
  let model = make_model(router.Records(filters))
  let #(new_model, _eff) =
    main.update(model, store.OnRouteChange(router.RecordDetail("5")))
  new_model.previous_route |> should.equal(Some(router.Records(dict.new())))
}
