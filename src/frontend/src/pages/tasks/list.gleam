// Tasks list page stub
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Tasks")]),
    html.p([], [html.text("Tasks list will be implemented here.")]),
  ])
}
