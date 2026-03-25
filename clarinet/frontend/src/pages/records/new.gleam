// New record creation page (admin only)
import components/forms/record_form
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("New Record")]),
    record_form.view(model),
  ])
}
