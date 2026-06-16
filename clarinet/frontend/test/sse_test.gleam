//// Transition tests for the SSE coordinator.
////
//// Transitions that carry an `EventSource` (Opened, MessageReceived,
//// WatchdogTick / Stop from Active) cannot be unit-tested: `EventSource` is an
//// opaque FFI type with no constructor outside a browser (same limitation as
//// `ViewerWindow` in preload_test). They are covered by manual / e2e checks.

import gleeunit/should
import sse
import utils/event_source

fn connecting() -> sse.Model {
  sse.Model(..sse.init(), state: sse.Connecting)
}

pub fn connect_from_idle_test() {
  let #(model, _eff, out) = sse.update(sse.init(), sse.Connect)
  model.state |> should.equal(sse.Connecting)
  out |> should.equal([])
}

pub fn connect_when_not_idle_is_noop_test() {
  let #(model, _eff, out) = sse.update(connecting(), sse.Connect)
  model.state |> should.equal(sse.Connecting)
  out |> should.equal([])
}

pub fn errored_closed_goes_idle_test() {
  let #(model, _eff, out) =
    sse.update(connecting(), sse.Event(event_source.Errored(2)))
  model.state |> should.equal(sse.Idle)
  // CLOSED does not trigger Logout — only an auth_expired frame does.
  out |> should.equal([])
}

pub fn errored_connecting_keeps_trying_test() {
  let #(model, _eff, out) =
    sse.update(sse.init(), sse.Event(event_source.Errored(0)))
  model.state |> should.equal(sse.Connecting)
  out |> should.equal([])
}

pub fn watchdog_tick_when_idle_is_noop_test() {
  let #(model, _eff, out) = sse.update(sse.init(), sse.WatchdogTick)
  model.state |> should.equal(sse.Idle)
  out |> should.equal([])
}

pub fn stop_from_connecting_resets_test() {
  let model = sse.Model(..sse.init(), state: sse.Connecting, has_connected_once: True)
  let #(new_model, _eff, out) = sse.update(model, sse.Stop)
  new_model |> should.equal(sse.init())
  out |> should.equal([])
}
