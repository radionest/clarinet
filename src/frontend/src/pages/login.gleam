// Login page
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}
import utils/dom

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("login-page")], [
    html.div([attribute.class("login-container")], [
      html.div([attribute.class("login-card card")], [
        html.h1([attribute.class("login-title")], [html.text("Clarinet")]),
        html.p([attribute.class("login-subtitle text-muted")], [
          html.text("Medical Imaging Framework"),
        ]),
        login_form(model),
        html.div([attribute.class("login-footer")], [
          html.p([attribute.class("text-muted")], [
            html.text("Don't have an account? "),
            html.a(
              [
                attribute.href("#"),
                event.on_click(store.Navigate(router.Register)),
              ],
              [html.text("Register here")],
            ),
          ]),
        ]),
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
          },
        ],
      ),
    ],
  )
}

// Handle form submission
fn handle_submit() -> Msg {
  // Get form values using native Gleam DOM utilities
  let username = case dom.get_input_value("username") {
    Some(value) -> value
    None -> ""
  }
  let password = case dom.get_input_value("password") {
    Some(value) -> value
    None -> ""
  }
  store.LoginSubmit(username, password)
}
