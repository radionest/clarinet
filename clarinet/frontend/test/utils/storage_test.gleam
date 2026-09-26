// Unit tests for utils/storage key namespacing
import gleam/string
import gleeunit/should
import utils/storage

pub fn root_keeps_legacy_prefix_test() {
  storage.prefix_for("") |> should.equal("clarinet:")
}

pub fn sub_path_prefix_test() {
  storage.prefix_for("/nir_liver") |> should.equal("clarinet/nir_liver:")
}

// The logout sweep removes every key starting with the current project's
// prefix — it must never match a sibling project's keys on the same origin.
pub fn prefixes_are_disjoint_test() {
  let swept_by = fn(owner: String, key_base: String) {
    string.starts_with(
      storage.prefix_for(key_base) <> "client_settings",
      storage.prefix_for(owner),
    )
  }
  swept_by("/a", "/a") |> should.be_true
  swept_by("", "/a") |> should.be_false
  swept_by("/a", "") |> should.be_false
  swept_by("/a", "/a_b") |> should.be_false
  swept_by("/a", "/a/b") |> should.be_false
}
