//// Thin FFI wrapper around the browser `EventSource` (SSE transport).
////
//// Native `EventSource` reconnects on its own (interval set by the server's
//// `retry:` line), so there is no manual backoff here — only open/message/error
//// callbacks. There is no client->server channel, so `send` is not implemented.

import gleam/bool
import lustre
import lustre/effect.{type Effect}

pub type EventSource

pub type Event {
  Opened(EventSource)
  MessageReceived(String)
  /// readyState: 0 = CONNECTING (auto-reconnecting), 2 = CLOSED (permanent).
  Errored(ready_state: Int)
}

/// Open an SSE connection. The callbacks live for the lifetime of the source
/// and dispatch many times. Guarded so gleeunit never calls `new EventSource`.
pub fn connect(path: String, to_msg: fn(Event) -> msg) -> Effect(msg) {
  use dispatch <- effect.from
  use <- bool.guard(!lustre.is_browser(), Nil)
  do_connect(
    path,
    fn(es) { dispatch(to_msg(Opened(es))) },
    fn(text) { dispatch(to_msg(MessageReceived(text))) },
    fn(state) { dispatch(to_msg(Errored(state))) },
  )
}

pub fn close(source: EventSource) -> Effect(msg) {
  use _dispatch <- effect.from
  do_close(source)
}

@external(javascript, "./event_source.ffi.mjs", "connect")
fn do_connect(
  path: String,
  on_open: fn(EventSource) -> Nil,
  on_message: fn(String) -> Nil,
  on_error: fn(Int) -> Nil,
) -> Nil

@external(javascript, "./event_source.ffi.mjs", "closeSource")
fn do_close(source: EventSource) -> Nil
