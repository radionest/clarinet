// Registration page
import gleam/option.{None, Some}
import gleam/string
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}
import utils/dom

pub fn view(model: Model) -> Element(Msg) {
  html.div([attribute.class("register-page")], [
    html.div([attribute.class("register-container")], [
      html.div([attribute.class("register-card card")], [
        html.h1([attribute.class("register-title")], [
          html.text("Create Account"),
        ]),
        html.p([attribute.class("register-subtitle text-muted")], [
          html.text("Register for Clarinet Medical Imaging Framework"),
        ]),
        register_form(model),
        html.div([attribute.class("register-footer")], [
          html.p([attribute.class("text-muted")], [
            html.text("Already have an account? "),
            html.a(
              [
                attribute.href("#"),
                event.on_click(store.Navigate(router.Login)),
              ],
              [html.text("Login here")],
            ),
          ]),
        ]),
      ]),
    ]),
  ])
}

fn register_form(model: Model) -> Element(Msg) {
  html.form(
    [
      attribute.class("register-form"),
      event.on_submit(fn(_) { handle_submit() }),
    ],
    [
      // Username field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("reg-username")], [html.text("Username")]),
        html.input([
          attribute.type_("text"),
          attribute.id("reg-username"),
          attribute.name("username"),
          attribute.placeholder("Choose a username"),
          attribute.required(True),
          attribute.disabled(model.loading),
        ]),
        html.small([attribute.class("form-text text-muted")], [
          html.text("Your unique identifier in the system"),
        ]),
      ]),

      // Email field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("reg-email")], [html.text("Email")]),
        html.input([
          attribute.type_("email"),
          attribute.id("reg-email"),
          attribute.name("email"),
          attribute.placeholder("your.email@example.com"),
          attribute.required(True),
          attribute.disabled(model.loading),
        ]),
        html.small([attribute.class("form-text text-muted")], [
          html.text("We'll use this for important notifications"),
        ]),
      ]),

      // Password field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("reg-password")], [html.text("Password")]),
        html.input([
          attribute.type_("password"),
          attribute.id("reg-password"),
          attribute.name("password"),
          attribute.placeholder("Enter a strong password"),
          attribute.required(True),
          attribute.disabled(model.loading),
          attribute.attribute("minlength", "8"),
        ]),
        html.small([attribute.class("form-text text-muted")], [
          html.text("Minimum 8 characters"),
        ]),
      ]),

      // Password confirmation field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("reg-password-confirm")], [
          html.text("Confirm Password"),
        ]),
        html.input([
          attribute.type_("password"),
          attribute.id("reg-password-confirm"),
          attribute.name("password-confirm"),
          attribute.placeholder("Re-enter your password"),
          attribute.required(True),
          attribute.disabled(model.loading),
        ]),
      ]),

      // Error message
      case model.error {
        Some(error) ->
          html.div([attribute.class("alert alert-danger")], [html.text(error)])
        None -> html.text("")
      },

      // Success message
      case model.success_message {
        Some(message) ->
          html.div([attribute.class("alert alert-success")], [
            html.text(message),
          ])
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
            True -> html.text("Creating account...")
            False -> html.text("Register")
          },
        ],
      ),
    ],
  )
}

// Handle form submission
fn handle_submit() -> Msg {
  // Get form values using native Gleam DOM utilities
  let username = case dom.get_input_value("reg-username") {
    Some(value) -> value
    None -> ""
  }
  let email = case dom.get_input_value("reg-email") {
    Some(value) -> value
    None -> ""
  }
  let password = case dom.get_input_value("reg-password") {
    Some(value) -> value
    None -> ""
  }
  let password_confirm = case dom.get_input_value("reg-password-confirm") {
    Some(value) -> value
    None -> ""
  }

  // Basic client-side validation
  case password == password_confirm {
    False -> store.SetError(Some("Passwords do not match"))
    True -> {
      case string.length(password) < 8 {
        True -> store.SetError(Some("Password must be at least 8 characters"))
        False -> store.RegisterSubmit(username, email, password)
      }
    }
  }
}
