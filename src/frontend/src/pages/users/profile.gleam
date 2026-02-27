// User profile page stub
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(_model: Model, id: String) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("User Profile")]),
    html.p([], [html.text("User #" <> id <> " profile will be displayed here.")]),
  ])
}
