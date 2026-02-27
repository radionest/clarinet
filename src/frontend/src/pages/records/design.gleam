// Record type design page stub
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(_model: Model, id: Option(String)) -> Element(Msg) {
  let title = case id {
    Some(record_type_id) -> "Edit Record Type #" <> record_type_id
    None -> "New Record Type"
  }

  html.div([attribute.class("container")], [
    html.h1([], [html.text(title)]),
    html.p([], [html.text("Record type form will be implemented here.")]),
  ])
}
