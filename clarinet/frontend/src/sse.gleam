//// Self-contained MVU coordinator for the SSE connection.
////
//// Simpler than a WebSocket client because `EventSource` reconnects natively:
//// there is no manual backoff, attempt counter, or reconnect timer — only a
//// watchdog (anti-zombie: if no frame, including ping, arrives within 90s the
//// half-open socket is force-closed and a fresh source opened).

import api/sse_events
import config
import gleam/dynamic
import gleam/option.{type Option, None, Some}
import lustre/effect.{type Effect}
import plinth/javascript/global
import utils/event_source
import utils/logger
import utils/time

const watchdog_ms = 90_000

pub type State {
  Idle
  Connecting
  Active(event_source.EventSource)
}

pub type Model {
  Model(
    state: State,
    has_connected_once: Bool,
    watchdog: Option(global.TimerID),
    // Wall-clock ms of the last received frame. The watchdog clears the timer
    // id best-effort, but a burst of frames can leave a stale timer pending;
    // WatchdogTick re-checks real idle against this so such a timer is a no-op.
    last_frame_ms: Int,
  )
}

pub type Msg {
  Connect
  Event(event_source.Event)
  /// Stores the watchdog TimerID returned by the arm effect (cf. preload.SetTimer).
  SetWatchdog(global.TimerID)
  WatchdogTick
  Stop
}

pub type OutMsg {
  SseConnected(reconnected: Bool)
  SseEntityEvent(sse_events.EntityEvent)
  SseTaskProgress(task: String, task_id: String, payload: dynamic.Dynamic)
  SsePresence(user_id: String, online: Bool)
  SseAuthExpired
}

pub fn init() -> Model {
  Model(state: Idle, has_connected_once: False, watchdog: None, last_frame_ms: 0)
}

pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    Connect ->
      case model.state {
        Idle -> #(
          Model(..model, state: Connecting),
          event_source.connect(config.base_path() <> "/api/events", Event),
          [],
        )
        _ -> #(model, effect.none(), [])
      }

    SetWatchdog(id) -> #(Model(..model, watchdog: Some(id)), effect.none(), [])

    Event(event_source.Opened(es)) -> #(
      Model(
        state: Active(es),
        has_connected_once: True,
        watchdog: None,
        last_frame_ms: time.now_ms(),
      ),
      arm_watchdog(model.watchdog),
      [SseConnected(reconnected: model.has_connected_once)],
    )

    Event(event_source.MessageReceived(text)) ->
      handle_frame(model, text)

    // CLOSED (401/503/404): stop, no reconnect, no Logout (see plan deviation #8).
    Event(event_source.Errored(2)) -> #(
      Model(..model, state: Idle, watchdog: None),
      clear_watchdog(model),
      [],
    )

    // CONNECTING: the browser reconnects on its own — keep the watchdog armed.
    Event(event_source.Errored(_)) -> #(
      Model(..model, state: Connecting),
      effect.none(),
      [],
    )

    WatchdogTick ->
      case model.state {
        Active(es) ->
          // A burst of frames can leave a stale watchdog timer pending; only
          // act on genuine idle (no frame for watchdog_ms), else it's a no-op.
          case time.now_ms() - model.last_frame_ms >= watchdog_ms {
            True -> #(
              Model(..model, state: Idle, watchdog: None),
              effect.batch([event_source.close(es), dispatch(Connect)]),
              [],
            )
            False -> #(model, effect.none(), [])
          }
        _ -> #(model, effect.none(), [])
      }

    Stop -> {
      let close_eff = case model.state {
        Active(es) -> event_source.close(es)
        _ -> effect.none()
      }
      #(init(), effect.batch([clear_watchdog(model), close_eff]), [])
    }
  }
}

fn handle_frame(model: Model, text: String) -> #(Model, Effect(Msg), List(OutMsg)) {
  let now = time.now_ms()
  case sse_events.decode_frame(text) {
    Ok(sse_events.Entity(e)) -> #(
      Model(..model, watchdog: None, last_frame_ms: now),
      arm_watchdog(model.watchdog),
      [SseEntityEvent(e)],
    )
    Ok(sse_events.TaskProgress(task, task_id, payload)) -> #(
      Model(..model, watchdog: None, last_frame_ms: now),
      arm_watchdog(model.watchdog),
      [SseTaskProgress(task, task_id, payload)],
    )
    Ok(sse_events.Presence(user_id, online)) -> #(
      Model(..model, watchdog: None, last_frame_ms: now),
      arm_watchdog(model.watchdog),
      [SsePresence(user_id, online)],
    )
    Ok(sse_events.AuthExpired) -> {
      let close_eff = case model.state {
        Active(es) -> event_source.close(es)
        _ -> effect.none()
      }
      #(
        Model(..model, state: Idle, watchdog: None),
        effect.batch([clear_watchdog(model), close_eff]),
        [SseAuthExpired],
      )
    }
    Ok(sse_events.Ping) -> #(
      Model(..model, watchdog: None, last_frame_ms: now),
      arm_watchdog(model.watchdog),
      [],
    )
    Error(Nil) -> #(
      Model(..model, watchdog: None, last_frame_ms: now),
      effect.batch([arm_watchdog(model.watchdog), log_bad_frame(text)]),
      [],
    )
  }
}

/// Close the live source and cancel the watchdog. Used by main on logout,
/// computed from the pre-reset model so the EventSource handle isn't lost.
pub fn cleanup(model: Model) -> Effect(Msg) {
  let close_eff = case model.state {
    Active(es) -> event_source.close(es)
    _ -> effect.none()
  }
  effect.batch([clear_watchdog(model), close_eff])
}

// --- Effects / helpers ---

fn arm_watchdog(old: Option(global.TimerID)) -> Effect(Msg) {
  use dispatch <- effect.from
  case old {
    Some(id) -> global.clear_timeout(id)
    None -> Nil
  }
  let new_id = global.set_timeout(watchdog_ms, fn() { dispatch(WatchdogTick) })
  dispatch(SetWatchdog(new_id))
}

fn clear_watchdog(model: Model) -> Effect(Msg) {
  case model.watchdog {
    Some(id) -> effect.from(fn(_dispatch) { global.clear_timeout(id) })
    None -> effect.none()
  }
}

fn dispatch(msg: Msg) -> Effect(Msg) {
  use d <- effect.from
  d(msg)
}

fn log_bad_frame(text: String) -> Effect(Msg) {
  use _dispatch <- effect.from
  logger.warn("sse", "Failed to decode frame: " <> text)
}
