// Task design page stub
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(model: Model, id: Option(String)) -> Element(Msg) {
  let title = case id {
    Some(task_id) -> "Edit Task Design #" <> task_id
    None -> "New Task Design"
  }

  html.div([attribute.class("container")], [
    html.h1([], [html.text(title)]),
    html.p([], [html.text("Task design form will be implemented here.")]),
  ])
}
