// Login page — self-contained MVU module
import api/auth
import api/models
import api/types
import gleam/javascript/promise
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}

// --- Model ---

pub type Model {
  Model(email: String, password: String, loading: Bool, error: Option(String))
}

// --- Msg ---

pub type Msg {
  UpdateEmail(String)
  UpdatePassword(String)
  Submit
  LoginResult(Result(models.LoginResponse, types.ApiError))
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  #(Model(email: "", password: "", loading: False, error: None), effect.none(), [])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    UpdateEmail(value) ->
      #(Model(..model, email: value), effect.none(), [])

    UpdatePassword(value) ->
      #(Model(..model, password: value), effect.none(), [])

    Submit -> {
      let eff = {
        use dispatch <- effect.from
        auth.login(model.email, model.password)
        |> promise.tap(fn(result) { dispatch(LoginResult(result)) })
        Nil
      }
      #(Model(..model, loading: True, error: None), eff, [])
    }

    LoginResult(Ok(response)) ->
      #(Model(..model, loading: False), effect.none(), [
        shared.SetUser(response.user),
        shared.Navigate(router.Home),
      ])

    LoginResult(Error(err)) -> {
      let error_msg = case err {
        types.AuthError(msg) -> msg
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Login failed. Please try again."
      }
      #(Model(..model, loading: False, error: Some(error_msg)), effect.none(), [])
    }
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("login-page")], [
    html.div([attribute.class("login-container")], [
      html.div([attribute.class("login-card card")], [
        html.h1([attribute.class("login-title")], [
          html.text(shared.project_name),
        ]),
        html.p([attribute.class("login-subtitle text-muted")], [
          html.text(shared.project_description),
        ]),
        login_form(model),
        html.div([attribute.class("login-footer")], [
          html.p([attribute.class("text-muted")], [
            html.text("Don't have an account? "),
            html.a(
              [attribute.href(router.route_to_path(router.Register))],
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
    [attribute.class("login-form"), event.on_submit(fn(_) { Submit })],
    [
      // Email field
      html.div([attribute.class("form-group")], [
        html.label([attribute.for("email"), attribute.class("form-label")], [
          html.text("Email"),
        ]),
        html.input([
          attribute.class("form-input"),
          attribute.type_("email"),
          attribute.id("email"),
          attribute.name("email"),
          attribute.placeholder("Enter your email"),
          attribute.value(model.email),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(UpdateEmail),
        ]),
      ]),
      // Password field
      html.div([attribute.class("form-group")], [
        html.label(
          [attribute.for("password"), attribute.class("form-label")],
          [html.text("Password")],
        ),
        html.input([
          attribute.class("form-input"),
          attribute.type_("password"),
          attribute.id("password"),
          attribute.name("password"),
          attribute.placeholder("Enter password"),
          attribute.value(model.password),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(UpdatePassword),
        ]),
      ]),
      // Error message
      case model.error {
        Some(error) ->
          html.div([attribute.class("error")], [html.text(error)])
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
