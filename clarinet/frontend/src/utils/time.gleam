//// Small time helpers shared across modules.

import gleam/time/timestamp

/// Current wall-clock time in milliseconds since the Unix epoch.
pub fn now_ms() -> Int {
  let #(seconds, nanoseconds) =
    timestamp.system_time()
    |> timestamp.to_unix_seconds_and_nanoseconds()
  seconds * 1000 + nanoseconds / 1_000_000
}
