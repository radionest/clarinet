// Login page
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}

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
      event.on_submit(fn(_) {
        store.LoginSubmit(model.login_email, model.login_password)
      }),
    ],
    [
      // Email field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("email")], [html.text("Email")]),
        html.input([
          attribute.type_("email"),
          attribute.id("email"),
          attribute.name("email"),
          attribute.placeholder("Enter your email"),
          attribute.value(model.login_email),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(store.LoginUpdateEmail),
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
          attribute.value(model.login_password),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(store.LoginUpdatePassword),
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
