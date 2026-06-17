// Admin Activity page — server-wide audit feed (record events + pipeline runs).
// A thin host around the reusable `activity_feed` sub-component: it owns the
// page chrome and forwards everything else to the feed.
import clarinet_frontend/i18n
import components/activity_feed
import gleam/dict
import gleam/list
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import shared.{type OutMsg, type Shared}
import utils/record_filters

// --- Model ---

pub type Model {
  Model(activity: activity_feed.Model)
}

// --- Msg ---

pub type Msg {
  ActivityMsg(activity_feed.Msg)
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let #(activity, eff, out) = activity_feed.init(activity_feed.GlobalSource)
  // Load users + record types so the events filter dropdowns have options.
  let out_msgs = [
    shared.ReloadUsers,
    shared.ReloadRecordTypes,
    ..shared.activity_out(out)
  ]
  #(Model(activity: activity), effect.map(eff, ActivityMsg), out_msgs)
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    ActivityMsg(sub_msg) -> {
      let #(activity, eff, out) = activity_feed.update(model.activity, sub_msg)
      #(Model(activity: activity), effect.map(eff, ActivityMsg), shared.activity_out(out))
    }
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text(shared.translate(i18n.NavActivity))]),
    ]),
    element.map(
      activity_feed.view(
        model.activity,
        shared.translate,
        actor_filter_options(shared),
        record_type_filter_options(shared),
      ),
      ActivityMsg,
    ),
  ])
}

/// Actor dropdown options for the events filter, built from the users cache
/// (sorted by email). The feed stays decoupled from `shared`, so the host
/// resolves the options and passes them in.
fn actor_filter_options(shared: Shared) -> List(#(String, String)) {
  let users = shared.cache.users
  let ids =
    users
    |> dict.values
    |> list.sort(fn(a, b) { string.compare(a.email, b.email) })
    |> list.map(fn(u) { u.id })
  record_filters.user_options(ids, users, shared.translate)
}

/// Record-type dropdown options for the events filter, built from the record
/// types cache (sorted by name).
fn record_type_filter_options(shared: Shared) -> List(#(String, String)) {
  shared.cache.record_types
  |> dict.keys
  |> list.sort(string.compare)
  |> record_filters.type_options(shared.translate)
}
