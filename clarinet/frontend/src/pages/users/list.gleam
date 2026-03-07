// Users list page stub
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(_model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("Users")]),
    html.p([], [html.text("Users list will be implemented here.")]),
  ])
}
