// Settings page — per-client (per-browser) Clarinet settings.
//
// Currently exposes only `storage_path_client`: the Slicer-visible storage
// prefix that replaces the server-side POSIX prefix when scripts run on the
// user's local Slicer. Stored in localStorage; injected into every API
// request as the `X-Clarinet-Storage-Path-Client` header by
// `api/http_client.build_request`.

import components/forms/base as forms
import gleam/dict
import gleam/option.{None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import shared.{type OutMsg, type Shared}
import utils/client_settings.{type ClientSettings}

// --- Model ---

pub type Model {
  Model(settings: ClientSettings, draft_storage_path: String)
}

// --- Msg ---

pub type Msg {
  UpdateStoragePath(String)
  Save
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let settings = client_settings.load_sync()
  let draft = case settings.storage_path_client {
    Some(v) -> v
    None -> ""
  }
  #(Model(settings:, draft_storage_path: draft), effect.none(), [])
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
        Model(settings: new_settings, draft_storage_path: model.draft_storage_path),
        client_settings.save(new_settings),
        [shared.ShowSuccess("Settings saved")],
      )
    }
  }
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
