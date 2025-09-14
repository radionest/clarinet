// Study detail page stub
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import store.{type Model, type Msg}

pub fn view(model: Model, id: String) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Study Detail")]),
    html.p([], [html.text("Study #" <> id <> " details will be displayed here.")]),
  ])
}