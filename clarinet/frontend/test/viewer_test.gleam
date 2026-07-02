import gleeunit/should
import utils/viewer

pub fn preload_enabled_for_builtin_test() {
  viewer.ohif_preload_enabled("builtin") |> should.be_true
}

pub fn preload_disabled_for_external_test() {
  viewer.ohif_preload_enabled("external") |> should.be_false
}
