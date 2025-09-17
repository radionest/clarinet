// Login page
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import lustre/event
import gleam/option.{None, Some}
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("login-page")], [
    html.div([attribute.class("login-container")], [
      html.div([attribute.class("login-card card")], [
        html.h1([attribute.class("login-title")], [html.text("Clarinet")]),
        html.p([attribute.class("login-subtitle text-muted")], [
          html.text("Medical Imaging Framework")
        ]),
        login_form(model),
      ]),
    ]),
  ])
}

fn login_form(model: Model) -> Element(Msg) {
  html.form(
    [
      attribute.class("login-form"),
      event.on_submit(fn(_) { handle_submit() }),
    ],
    [
      // Username field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("username")], [html.text("Username")]),
        html.input([
          attribute.type_("text"),
          attribute.id("username"),
          attribute.name("username"),
          attribute.placeholder("Enter username"),
          attribute.required(True),
          attribute.disabled(model.loading),
        ]),
      ]),

      // Password field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("password")], [html.text("Password")]),
        html.input([
          attribute.type_("password"),
          attribute.id("password"),
          attribute.name("password"),
          attribute.placeholder("Enter password"),
          attribute.required(True),
          attribute.disabled(model.loading),
        ]),
      ]),

      // Error message
      case model.error {
        Some(error) -> html.div([attribute.class("error")], [html.text(error)])
        None -> html.text("")
      },

      // Submit button
      html.button(
        [
          attribute.type_("submit"),
          attribute.class("btn btn-primary btn-block"),
          attribute.disabled(model.loading),
        ],
        [
          case model.loading {
            True -> html.text("Logging in...")
            False -> html.text("Login")
          }
        ]
      ),
    ]
  )
}

// Handle form submission
fn handle_submit() -> Msg {
  // Get form values using JavaScript FFI
  let username = get_input_value("username")
  let password = get_input_value("password")
  store.LoginSubmit(username, password)
}

// JavaScript FFI to get input value
@external(javascript, "../ffi/forms.js", "getInputValue")
fn get_input_value(id: String) -> String