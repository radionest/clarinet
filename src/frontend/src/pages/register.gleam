// Registration page
import gleam/option.{None, Some}
import gleam/string
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}

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
      event.on_submit(fn(_) { handle_submit(model) }),
    ],
    [
      // Email field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("reg-email")], [html.text("Email")]),
        html.input([
          attribute.type_("email"),
          attribute.id("reg-email"),
          attribute.name("email"),
          attribute.placeholder("your.email@example.com"),
          attribute.value(model.register_email),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(store.RegisterUpdateEmail),
        ]),
        html.small([attribute.class("form-text text-muted")], [
          html.text("This will be your unique identifier for login"),
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
          attribute.value(model.register_password),
          attribute.required(True),
          attribute.disabled(model.loading),
          attribute.attribute("minlength", "8"),
          event.on_input(store.RegisterUpdatePassword),
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
          attribute.value(model.register_password_confirm),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(store.RegisterUpdatePasswordConfirm),
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

// Handle form submission — reads from Model state
fn handle_submit(model: Model) -> Msg {
  case model.register_password == model.register_password_confirm {
    False -> store.SetError(Some("Passwords do not match"))
    True -> {
      case string.length(model.register_password) < 8 {
        True -> store.SetError(Some("Password must be at least 8 characters"))
        False -> store.RegisterSubmit(model.register_email, model.register_password)
      }
    }
  }
}
