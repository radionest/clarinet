// Studies list page stub
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Studies")]),
    html.p([], [html.text("Studies list will be implemented here.")]),
  ])
}
