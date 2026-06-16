// Settings page — per-client (per-browser) Clarinet settings plus the
// account's active-session list.
//
// `storage_path_client`: the Slicer-visible storage prefix that replaces the
// server-side POSIX prefix when scripts run on the user's local Slicer. Stored
// in localStorage; injected into every API request as the
// `X-Clarinet-Storage-Path-Client` header by `api/http_client.build_request`.
//
// Active sessions: a read-only table of the current user's sessions
// (GET /api/auth/sessions/active) with a per-row revoke button
// (DELETE /api/auth/sessions/{token_preview}). The current session is marked
// "This device" and cannot be revoked from here.

import api/auth
import api/models.{type SessionInfo}
import api/types.{type ApiError, AuthError}
import components/forms/base as forms
import gleam/dict
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import shared.{type OutMsg, type Shared}
import utils/client_settings.{type ClientSettings}
import utils/datetime
import utils/load_status.{type LoadStatus}

// --- Model ---

pub type Model {
  Model(
    settings: ClientSettings,
    draft_storage_path: String,
    sessions_status: LoadStatus,
    sessions: List(SessionInfo),
    // token_preview of the session whose revoke request is in flight; disables
    // its button so a double-click can't fire two DELETEs.
    revoking: Option(String),
  )
}

// --- Msg ---

pub type Msg {
  UpdateStoragePath(String)
  Save
  SessionsLoaded(Result(List(SessionInfo), ApiError))
  RetryLoadSessions
  RevokeSession(String)
  SessionRevoked(token_preview: String, result: Result(Nil, ApiError))
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let settings = client_settings.load_sync()
  let draft = case settings.storage_path_client {
    Some(v) -> v
    None -> ""
  }
  #(
    Model(
      settings:,
      draft_storage_path: draft,
      sessions_status: load_status.Loading,
      sessions: [],
      revoking: None,
    ),
    load_sessions_effect(),
    [],
  )
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    UpdateStoragePath(value) -> #(
      Model(..model, draft_storage_path: value),
      effect.none(),
      [],
    )

    Save -> {
      let new_settings =
        client_settings.with_storage_path(model.draft_storage_path)
      #(
        Model(..model, settings: new_settings),
        client_settings.save(new_settings),
        [shared.ShowSuccess("Settings saved")],
      )
    }

    SessionsLoaded(Ok(sessions)) -> #(
      Model(..model, sessions:, sessions_status: load_status.Loaded),
      effect.none(),
      [],
    )

    SessionsLoaded(Error(err)) -> #(
      Model(
        ..model,
        sessions_status: load_status.Failed("Failed to load sessions"),
      ),
      effect.none(),
      // Non-auth failures are shown inline (Failed status + retry); only a
      // session-killing 401 escalates to the host.
      auth_out(err),
    )

    RetryLoadSessions -> #(
      Model(..model, sessions_status: load_status.Loading),
      load_sessions_effect(),
      [],
    )

    RevokeSession(token_preview) -> #(
      Model(..model, revoking: Some(token_preview)),
      revoke_effect(token_preview),
      [],
    )

    SessionRevoked(token_preview, Ok(_)) -> #(
      Model(
        ..model,
        revoking: None,
        sessions: list.filter(model.sessions, fn(s) {
          s.token_preview != token_preview
        }),
      ),
      effect.none(),
      [shared.ShowSuccess("Session revoked")],
    )

    SessionRevoked(_token_preview, Error(err)) -> #(
      Model(..model, revoking: None),
      effect.none(),
      case err {
        AuthError(_) -> [shared.Logout]
        _ -> [shared.ShowError("Failed to revoke session")]
      },
    )
  }
}

fn auth_out(err: ApiError) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> []
  }
}

fn load_sessions_effect() -> Effect(Msg) {
  use dispatch <- effect.from
  auth.get_active_sessions()
  |> promise.tap(fn(result) { dispatch(SessionsLoaded(result)) })
  Nil
}

fn revoke_effect(token_preview: String) -> Effect(Msg) {
  use dispatch <- effect.from
  auth.revoke_session(token_preview)
  |> promise.tap(fn(result) { dispatch(SessionRevoked(token_preview, result)) })
  Nil
}

// --- View ---

pub fn view(model: Model, _shared: Shared) -> Element(Msg) {
  html.div([attribute.class("settings-page")], [
    html.div([attribute.class("settings-container")], [
      html.div([attribute.class("card")], [
        html.h1([attribute.class("settings-title")], [html.text("Settings")]),
        html.p([attribute.class("text-muted")], [
          html.text(
            "Per-device settings stored in this browser. They do not sync to "
            <> "other devices.",
          ),
        ]),
        settings_form(model),
      ]),
      sessions_card(model),
    ]),
  ])
}

fn settings_form(model: Model) -> Element(Msg) {
  forms.form(fn() { Save }, [
    forms.field_with_hint(
      label: "Slicer storage path (this device)",
      name: "storage_path_client",
      input: forms.text_input(
        name: "storage_path_client",
        value: model.draft_storage_path,
        placeholder: Some("//host/share or /mnt/share or smb://host/share"),
        on_input: UpdateStoragePath,
      ),
      errors: dict.new(),
      required: False,
      hint: Some(
        "Path to the Clarinet storage as seen by Slicer on this machine. "
        <> "Windows: //host/share (UNC). Linux: a mounted POSIX path "
        <> "(e.g. /mnt/share) or smb://host/share via GVFS. ASCII characters "
        <> "only — HTTP headers cannot carry Unicode reliably. Leave empty "
        <> "to use the server's default.",
      ),
    ),
    html.div([attribute.class("form-actions")], [
      forms.submit_button(text: "Save", disabled: False, on_click: None),
    ]),
  ])
}

fn sessions_card(model: Model) -> Element(Msg) {
  html.div([attribute.class("card")], [
    html.h2([attribute.class("settings-title")], [html.text("Active sessions")]),
    html.p([attribute.class("text-muted")], [
      html.text(
        "Devices and locations where your account is currently signed in. "
        <> "Revoke any session you don't recognize.",
      ),
    ]),
    load_status.render(
      model.sessions_status,
      fn() {
        html.p([attribute.class("text-muted")], [html.text("Loading sessions…")])
      },
      fn() { sessions_table(model) },
      fn(message) {
        html.div([attribute.class("error-container")], [
          html.p([attribute.class("error-message")], [html.text(message)]),
          html.button(
            [
              attribute.class("btn btn-primary"),
              event.on_click(RetryLoadSessions),
            ],
            [html.text("Retry")],
          ),
        ])
      },
    ),
  ])
}

fn sessions_table(model: Model) -> Element(Msg) {
  case model.sessions {
    [] ->
      html.p([attribute.class("text-muted")], [html.text("No active sessions.")])
    sessions ->
      html.div([attribute.class("table-responsive")], [
        html.table([attribute.class("table")], [
          html.thead([], [
            html.tr([], [
              html.th([], [html.text("IP address")]),
              html.th([], [html.text("Device")]),
              html.th([], [html.text("Last active")]),
              html.th([], [html.text("Status")]),
            ]),
          ]),
          html.tbody(
            [],
            list.map(sessions, fn(s) { session_row(s, model.revoking) }),
          ),
        ]),
      ])
  }
}

fn session_row(session: SessionInfo, revoking: Option(String)) -> Element(Msg) {
  html.tr([], [
    html.td([], [html.text(option.unwrap(session.ip_address, "—"))]),
    html.td([attribute.class("text-muted")], [
      html.text(option.unwrap(session.user_agent, "—")),
    ]),
    html.td([], [html.text(datetime.format(session.last_accessed))]),
    html.td([attribute.class("cell-actions")], [
      session_action(session, revoking),
    ]),
  ])
}

fn session_action(
  session: SessionInfo,
  revoking: Option(String),
) -> Element(Msg) {
  case session.is_current {
    True ->
      html.span([attribute.class("badge badge-info")], [
        html.text("This device"),
      ])
    False -> {
      let is_revoking = revoking == Some(session.token_preview)
      html.button(
        [
          attribute.class("btn btn-sm btn-danger"),
          attribute.disabled(is_revoking),
          event.on_click(RevokeSession(session.token_preview)),
        ],
        [
          html.text(case is_revoking {
            True -> "Revoking…"
            False -> "Revoke"
          }),
        ],
      )
    }
  }
}
