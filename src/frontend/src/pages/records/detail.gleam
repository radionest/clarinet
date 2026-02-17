// Record detail page stub
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(model: Model, id: String) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Record Detail")]),
    html.p([], [html.text("Record #" <> id <> " details will be displayed here.")]),
  ])
}
