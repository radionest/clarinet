// Base form elements and utilities
import gleam/dict.{type Dict}
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import store.{type Msg}

// Form field wrapper with label and error display
pub fn field(
  label label: String,
  name name: String,
  input input: Element(Msg),
  errors errors: Dict(String, String),
  required required: Bool,
) -> Element(Msg) {
  let class = case required {
    True -> "form-field required"
    False -> "form-field"
  }
  html.div([attribute.class(class)], [
    html.label([attribute.for(name), attribute.class("form-label")], [
      html.text(label),
      case required {
        True -> html.span([attribute.class("required-marker")], [html.text(" *")])
        False -> html.text("")
      },
    ]),
    input,
    error_message(errors, name),
  ])
}

// Text input
pub fn text_input(
  name name: String,
  value value: String,
  placeholder placeholder: Option(String),
  on_input on_input: fn(String) -> Msg,
) -> Element(Msg) {
  let attrs = [
    attribute.type_("text"),
    attribute.id(name),
    attribute.name(name),
    attribute.value(value),
    attribute.class("form-input"),
    event.on_input(on_input),
  ]

  let attrs = case placeholder {
    Some(p) -> list.append(attrs, [attribute.placeholder(p)])
    None -> attrs
  }

  html.input(attrs)
}

// Email input
pub fn email_input(
  name name: String,
  value value: String,
  placeholder placeholder: Option(String),
  on_input on_input: fn(String) -> Msg,
) -> Element(Msg) {
  let attrs = [
    attribute.type_("email"),
    attribute.id(name),
    attribute.name(name),
    attribute.value(value),
    attribute.class("form-input"),
    event.on_input(on_input),
  ]

  let attrs = case placeholder {
    Some(p) -> list.append(attrs, [attribute.placeholder(p)])
    None -> attrs
  }

  html.input(attrs)
}

// Password input
pub fn password_input(
  name name: String,
  value value: String,
  placeholder placeholder: Option(String),
  on_input on_input: fn(String) -> Msg,
) -> Element(Msg) {
  let attrs = [
    attribute.type_("password"),
    attribute.id(name),
    attribute.name(name),
    attribute.value(value),
    attribute.class("form-input"),
    event.on_input(on_input),
  ]

  let attrs = case placeholder {
    Some(p) -> list.append(attrs, [attribute.placeholder(p)])
    None -> attrs
  }

  html.input(attrs)
}

// Number input
pub fn number_input(
  name name: String,
  value value: Int,
  min min: Option(Int),
  max max: Option(Int),
  on_input on_input: fn(String) -> Msg,
) -> Element(Msg) {
  let attrs = [
    attribute.type_("number"),
    attribute.id(name),
    attribute.name(name),
    attribute.value(int.to_string(value)),
    attribute.class("form-input"),
    event.on_input(on_input),
  ]

  let attrs = case min {
    Some(m) -> list.append(attrs, [attribute.min(int.to_string(m))])
    None -> attrs
  }

  let attrs = case max {
    Some(m) -> list.append(attrs, [attribute.max(int.to_string(m))])
    None -> attrs
  }

  html.input(attrs)
}

// Date input
pub fn date_input(
  name name: String,
  value value: String,
  on_input on_input: fn(String) -> Msg,
) -> Element(Msg) {
  html.input([
    attribute.type_("date"),
    attribute.id(name),
    attribute.name(name),
    attribute.value(value),
    attribute.class("form-input"),
    event.on_input(on_input),
  ])
}

// Textarea
pub fn textarea(
  name name: String,
  value value: String,
  rows rows: Int,
  placeholder placeholder: Option(String),
  on_input on_input: fn(String) -> Msg,
) -> Element(Msg) {
  let attrs = [
    attribute.id(name),
    attribute.name(name),
    attribute.attribute("rows", int.to_string(rows)),
    attribute.class("form-textarea"),
    event.on_input(on_input),
  ]

  let attrs = case placeholder {
    Some(p) -> list.append(attrs, [attribute.placeholder(p)])
    None -> attrs
  }

  html.textarea(attrs, value)
}

// Select dropdown
pub fn select(
  name name: String,
  value value: String,
  options options: List(#(String, String)),
  on_change on_change: fn(String) -> Msg,
) -> Element(Msg) {
  html.select(
    [
      attribute.id(name),
      attribute.name(name),
      attribute.class("form-select"),
      event.on_change(on_change),
    ],
    list.map(options, fn(opt) {
      let #(val, label) = opt
      let attrs = [attribute.value(val)]
      let attrs = case val == value {
        True -> list.append(attrs, [attribute.selected(True)])
        False -> attrs
      }
      html.option(attrs, label)
    }),
  )
}

// Radio button group
pub fn radio_group(
  name name: String,
  value value: String,
  options options: List(#(String, String)),
  on_change on_change: fn(String) -> Msg,
) -> Element(Msg) {
  html.div(
    [attribute.class("form-radio-group")],
    list.map(options, fn(opt) {
      let #(val, label) = opt
      let input_id = name <> "_" <> val
      html.label([attribute.for(input_id), attribute.class("radio-label")], [
        html.input([
          attribute.type_("radio"),
          attribute.id(input_id),
          attribute.name(name),
          attribute.value(val),
          attribute.checked(val == value),
          attribute.class("radio-input"),
          event.on_click(on_change(val)),
        ]),
        html.span([attribute.class("radio-text")], [html.text(label)]),
      ])
    }),
  )
}

// Checkbox
pub fn checkbox(
  name name: String,
  checked checked: Bool,
  label label: String,
  on_change on_change: fn(Bool) -> Msg,
) -> Element(Msg) {
  html.label([attribute.for(name), attribute.class("checkbox-label")], [
    html.input([
      attribute.type_("checkbox"),
      attribute.id(name),
      attribute.name(name),
      attribute.checked(checked),
      attribute.class("checkbox-input"),
      event.on_check(on_change),
    ]),
    html.span([attribute.class("checkbox-text")], [html.text(label)]),
  ])
}

// Submit button
pub fn submit_button(
  text text: String,
  disabled disabled: Bool,
  on_click on_click: Option(Msg),
) -> Element(Msg) {
  let attrs = [
    attribute.type_("submit"),
    attribute.class("btn btn-primary"),
    attribute.disabled(disabled),
  ]

  let attrs = case on_click {
    Some(msg) -> list.append(attrs, [event.on_click(msg)])
    None -> attrs
  }

  html.button(attrs, [html.text(text)])
}

// Cancel button
pub fn cancel_button(text text: String, on_click on_click: Msg) -> Element(Msg) {
  html.button(
    [
      attribute.type_("button"),
      attribute.class("btn btn-secondary"),
      event.on_click(on_click),
    ],
    [html.text(text)],
  )
}

// Error message display
pub fn error_message(
  errors: Dict(String, String),
  field: String,
) -> Element(Msg) {
  case dict.get(errors, field) {
    Ok(error) -> html.span([attribute.class("form-error")], [html.text(error)])
    Error(_) -> html.text("")
  }
}

// Form wrapper with prevent default
pub fn form(
  on_submit: fn() -> Msg,
  children: List(Element(Msg)),
) -> Element(Msg) {
  html.form(
    [
      attribute.class("form"),
      event.on_submit(fn(_form_data) { on_submit() }),
    ],
    children,
  )
}

// Form row for horizontal layout
pub fn form_row(children: List(Element(Msg))) -> Element(Msg) {
  html.div([attribute.class("form-row")], children)
}

// Form group for related fields
pub fn form_group(title: String, children: List(Element(Msg))) -> Element(Msg) {
  html.fieldset([attribute.class("form-group")], [
    html.legend([attribute.class("form-group-title")], [html.text(title)]),
    ..children
  ])
}

// Loading indicator for forms
pub fn loading_overlay(loading: Bool) -> Element(Msg) {
  case loading {
    True ->
      html.div([attribute.class("form-loading-overlay")], [
        html.div([attribute.class("spinner")], []),
      ])
    False -> html.text("")
  }
}

// Success message
pub fn success_message(message: Option(String)) -> Element(Msg) {
  case message {
    Some(msg) -> html.div([attribute.class("form-success")], [html.text(msg)])
    None -> html.text("")
  }
}

// Helper to validate required fields
pub fn validate_required(
  value value: String,
  field_name field_name: String,
) -> Result(String, String) {
  case string.is_empty(value) {
    True -> Error(field_name <> " is required")
    False -> Ok(value)
  }
}

// Helper to validate email
pub fn validate_email(email: String) -> Result(String, String) {
  case string.contains(email, "@") && string.contains(email, ".") {
    True -> Ok(email)
    False -> Error("Invalid email format")
  }
}

// Import missing modules
import gleam/int
import gleam/string
