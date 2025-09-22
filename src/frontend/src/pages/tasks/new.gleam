// New task page stub
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("New Task")]),
    html.p([], [html.text("New task form will be implemented here.")]),
  ])
}
