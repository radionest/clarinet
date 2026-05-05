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

pub fn from_backend_string_round_trips_paused_test() {
  // to_backend_string emits "paused"; from_backend_string must accept it.
  // Regression guard: the original AdminStats payload shipped "pause"
  // (no 'd'), so from_backend_string also accepts that historical form.
  let s = status.to_backend_string(types.Paused)
  status.from_backend_string(s) |> should.equal(types.Paused)
}

pub fn from_backend_string_accepts_legacy_pause_test() {
  status.from_backend_string("pause") |> should.equal(types.Paused)
}
