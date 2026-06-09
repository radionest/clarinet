// Typed wrapper over utils/storage.gleam for per-client Clarinet settings.
// Values survive browser close (storage.Local backend) but never sync between
// browsers — that's intentional: the Slicer storage path depends on how the
// client OS sees the share, so a single user on Windows and Linux machines
// keeps two different values, one per device.

import gleam/dict
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/string
import lustre/effect.{type Effect}
import utils/storage

const settings_key = "client_settings"

const storage_path_field = "storage_path_client"

/// Per-client settings persisted in this browser's localStorage.
pub type ClientSettings {
  ClientSettings(storage_path_client: Option(String))
}

pub fn default() -> ClientSettings {
  ClientSettings(storage_path_client: None)
}

/// Read settings from localStorage. Returns `default()` on any error
/// (missing key, malformed JSON, empty value, etc.) so callers never
/// need a Result.
pub fn load_sync() -> ClientSettings {
  let data = storage.load_dict_sync(storage.Local, settings_key)
  ClientSettings(
    storage_path_client: dict.get(data, storage_path_field)
      |> result.unwrap("")
      |> non_empty,
  )
}

/// Persist settings to localStorage. Fire-and-forget effect.
pub fn save(settings: ClientSettings) -> Effect(msg) {
  let entries = case settings.storage_path_client {
    Some(p) -> [#(storage_path_field, p)]
    None -> []
  }
  storage.save_dict(storage.Local, settings_key, dict.from_list(entries))
}

/// Build `ClientSettings` carrying only `storage_path_client`. Whitespace is
/// trimmed; an empty input clears the field (so a blank value falls back to
/// the server-side `settings.storage_path_client` legacy default).
///
/// No `settings` parameter: `ClientSettings` currently holds a single field,
/// and ignoring an "existing" value would silently zero out any future
/// additions. When more per-device settings appear, switch this to an
/// updater that uses record-update syntax (`ClientSettings(..settings, ...)`).
pub fn with_storage_path(path: String) -> ClientSettings {
  ClientSettings(storage_path_client: non_empty(string.trim(path)))
}

fn non_empty(value: String) -> Option(String) {
  case value {
    "" -> None
    v -> Some(v)
  }
}
