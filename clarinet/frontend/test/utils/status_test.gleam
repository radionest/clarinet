// Unit tests for utils/status — exhaustive colour map and round-trip
// guard for the historical "pause" / "paused" backend string mismatch.
import api/types
import gleeunit/should
import utils/status

pub fn color_returns_expected_tailwind_classes_test() {
  status.color(types.Blocked) |> should.equal("yellow")
  status.color(types.Pending) |> should.equal("blue")
  status.color(types.InWork) |> should.equal("orange")
  status.color(types.Finished) |> should.equal("green")
  status.color(types.Failed) |> should.equal("red")
  status.color(types.Paused) |> should.equal("gray")
}

pub fn paused_round_trips_through_backend_canonical_test() {
  // Backend canonical for Paused is "pause" (no 'd'). Regression guard:
  // emitting "paused" silently breaks the /admin status dropdown and the
  // /records ?status= URL filter against a real backend.
  status.to_backend_string(types.Paused) |> should.equal("pause")
  status.from_backend_string("pause") |> should.equal(types.Paused)
}

pub fn from_backend_string_accepts_legacy_paused_test() {
  // Stale URL/localStorage state from before unification.
  status.from_backend_string("paused") |> should.equal(types.Paused)
}
