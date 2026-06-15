// Admin Activity page — server-wide audit feed (record events + pipeline runs).
// A thin host around the reusable `activity_feed` sub-component: it owns the
// page chrome and forwards everything else to the feed.
import clarinet_frontend/i18n
import components/activity_feed
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import shared.{type OutMsg, type Shared}

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
  #(Model(activity: activity), effect.map(eff, ActivityMsg), shared.activity_out(out))
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
      activity_feed.view(model.activity, shared.translate),
      ActivityMsg,
    ),
  ])
}
