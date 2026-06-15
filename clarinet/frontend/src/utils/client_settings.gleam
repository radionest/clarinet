// Typed wrapper over utils/storage.gleam for per-client Clarinet settings.
// Values survive browser close (storage.Local backend) but never sync between
// browsers — that's intentional: the Slicer storage path depends on how the
// client OS sees the share, so a single user on Windows and Linux machines
// keeps two different values, one per device.

import config
import gleam/dict
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/string
import lustre/effect.{type Effect}
import utils/cookie
import utils/storage

/// localStorage key (without the `clarinet:` prefix) under which these
/// per-device settings live. Exposed so the logout flow can preserve it
/// while clearing the rest of the namespace.
pub const settings_key = "client_settings"

const storage_path_field = "storage_path_client"

/// Cookie carrying `storage_path_client` to the backend. localStorage stays the
/// source for the settings input; this cookie is the transport for requests the
/// frontend's HTTP client does not build (formosh form-submits drop custom
/// headers). Read by `get_client_storage_path` (header-first, cookie-fallback).
const cookie_name = "clarinet_storage_path_client"

/// 1 year — the path is device-bound and rarely changes.
const cookie_max_age_seconds = 31_536_000

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

/// Persist settings to localStorage **and** mirror the storage path into the
/// `clarinet_storage_path_client` cookie (so it rides on formosh form-submits
/// that strip custom headers). Fire-and-forget effect.
///
/// When all fields are `None`, removes the localStorage key entirely instead of
/// writing `{}` — avoids a "settings exist but are empty" sentinel that
/// complicates future migrations and gives a false signal to consumers checking
/// `load_sync()` against `default()` — and expires the cookie. The cookie is
/// scoped to `config.base_path()` so it attaches to this app's `…/api/…` calls
/// and stays out of sibling apps deployed under other sub-paths.
pub fn save(settings: ClientSettings) -> Effect(msg) {
  let cookie_path = config.base_path() <> "/"
  case settings.storage_path_client {
    Some(p) ->
      effect.batch([
        storage.save_dict(
          storage.Local,
          settings_key,
          dict.from_list([#(storage_path_field, p)]),
        ),
        cookie.set_cookie(cookie_name, p, cookie_path, cookie_max_age_seconds),
      ])
    None ->
      effect.batch([
        storage.remove(storage.Local, settings_key),
        cookie.delete_cookie(cookie_name, cookie_path),
      ])
  }
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
