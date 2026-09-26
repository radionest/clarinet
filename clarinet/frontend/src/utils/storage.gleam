// Persistent key-value storage abstraction over plinth/javascript/storage.
// Keys written through this module are namespaced per project (`prefix_for`).
// `clarinet_locale` (main.gleam) deliberately bypasses it: UI language is a
// per-user choice shared by every project on the host.

import config
import gleam/dict.{type Dict}
import gleam/dynamic/decode
import gleam/json
import gleam/list
import gleam/result
import gleam/string
import lustre/effect.{type Effect}
import plinth/javascript/storage as plinth_storage

/// Storage backend.
pub type Backend {
  /// localStorage — survives tab/browser close
  Local
  /// sessionStorage — cleared on tab close
  Session
}

/// Key prefix for a deploy base path (`config.base_path()` form: "" or
/// "/nir_liver"). localStorage is per-origin, not per-path, so sibling
/// projects under sub-paths of one host would otherwise share every key.
/// Root keeps the historical "clarinet:"; the ":" terminator keeps "/a",
/// "/a_b" and "/a/b" disjoint for the logout sweep.
///
/// Unscoped "clarinet:*" keys left by sub-path deploys before this scoping
/// are never swept: on a shared origin they are indistinguishable from a
/// root deploy's live keys.
pub fn prefix_for(base_path: String) -> String {
  "clarinet" <> base_path <> ":"
}

fn prefix() -> String {
  prefix_for(config.base_path())
}

/// Save a Dict(String, String) as JSON. Fire-and-forget effect.
pub fn save_dict(
  backend: Backend,
  key: String,
  data: Dict(String, String),
) -> Effect(msg) {
  effect.from(fn(_dispatch) {
    let json_str =
      data
      |> dict.to_list()
      |> list.map(fn(pair) { #(pair.0, json.string(pair.1)) })
      |> json.object()
      |> json.to_string()
    let _ = case get_storage(backend) {
      Ok(s) -> plinth_storage.set_item(s, prefix() <> key, json_str)
      Error(_) -> Error(Nil)
    }
    Nil
  })
}

/// Load a Dict(String, String) from JSON. Falls back to empty dict on any error.
pub fn load_dict(
  backend: Backend,
  key: String,
  on_result: fn(Dict(String, String)) -> msg,
) -> Effect(msg) {
  effect.from(fn(dispatch) {
    let data =
      get_storage(backend)
      |> result.try(plinth_storage.get_item(_, prefix() <> key))
      |> result.try(fn(raw) {
        json.parse(raw, decode.dict(decode.string, decode.string))
        |> result.map_error(fn(_) { Nil })
      })
      |> result.unwrap(dict.new())
    dispatch(on_result(data))
  })
}

/// Synchronous read of a Dict(String, String) from storage.
/// Returns empty dict on any error. For use in page init where
/// the value is needed immediately (not via Effect).
pub fn load_dict_sync(backend: Backend, key: String) -> Dict(String, String) {
  get_storage(backend)
  |> result.try(plinth_storage.get_item(_, prefix() <> key))
  |> result.try(fn(raw) {
    json.parse(raw, decode.dict(decode.string, decode.string))
    |> result.map_error(fn(_) { Nil })
  })
  |> result.unwrap(dict.new())
}

/// Remove a specific key.
pub fn remove(backend: Backend, key: String) -> Effect(msg) {
  effect.from(fn(_dispatch) {
    case get_storage(backend) {
      Ok(s) -> plinth_storage.remove_item(s, prefix() <> key)
      Error(_) -> Nil
    }
  })
}

/// Remove all keys under this project's prefix (for logout cleanup).
pub fn clear_prefixed(backend: Backend) -> Effect(msg) {
  clear_prefixed_except(backend, [])
}

/// Remove all keys under this project's prefix EXCEPT the given keys
/// (without prefix). Use for logout flows that need to preserve
/// per-device settings (e.g. `client_settings` — Slicer storage path
/// is bound to this machine, not to the session).
pub fn clear_prefixed_except(
  backend: Backend,
  keep: List(String),
) -> Effect(msg) {
  effect.from(fn(_dispatch) {
    case get_storage(backend) {
      Ok(s) -> do_clear_prefixed(s, keep)
      Error(_) -> Nil
    }
  })
}

fn get_storage(backend: Backend) -> Result(plinth_storage.Storage, Nil) {
  case backend {
    Local -> plinth_storage.local()
    Session -> plinth_storage.session()
  }
}

// Iterate storage keys in reverse order and remove those starting with prefix.
// Reverse order avoids index shifting when removing items.
fn do_clear_prefixed(s: plinth_storage.Storage, keep: List(String)) -> Nil {
  let count = plinth_storage.length(s)
  let p = prefix()
  let keep_full = list.map(keep, fn(k) { p <> k })
  do_clear_prefixed_loop(s, count - 1, p, keep_full)
}

fn do_clear_prefixed_loop(
  s: plinth_storage.Storage,
  index: Int,
  p: String,
  keep_full: List(String),
) -> Nil {
  case index < 0 {
    True -> Nil
    False -> {
      case plinth_storage.key(s, index) {
        Ok(k) ->
          case string.starts_with(k, p) && !list.contains(keep_full, k) {
            True -> plinth_storage.remove_item(s, k)
            False -> Nil
          }
        Error(_) -> Nil
      }
      do_clear_prefixed_loop(s, index - 1, p, keep_full)
    }
  }
}
