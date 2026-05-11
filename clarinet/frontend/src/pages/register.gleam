// Registration page — self-contained MVU module
import api/auth
import api/models
import api/types
import gleam/bool
import gleam/javascript/promise
import gleam/option.{type Option, None, Some}
import gleam/string
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}

// --- Model ---

pub type Model {
  Model(
    email: String,
    password: String,
    password_confirm: String,
    loading: Bool,
    error: Option(String),
  )
}

// --- Msg ---

pub type Msg {
  UpdateEmail(String)
  UpdatePassword(String)
  UpdatePasswordConfirm(String)
  Submit
  RegisterResult(Result(models.User, types.ApiError))
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      email: "",
      password: "",
      password_confirm: "",
      loading: False,
      error: None,
    )
  #(model, effect.none(), [])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    UpdateEmail(value) ->
      #(Model(..model, email: value), effect.none(), [])

    UpdatePassword(value) ->
      #(Model(..model, password: value), effect.none(), [])

    UpdatePasswordConfirm(value) ->
      #(Model(..model, password_confirm: value), effect.none(), [])

    Submit -> {
      // Guard against double-submit — same rationale as login.Submit.
      use <- bool.lazy_guard(model.loading, fn() { #(model, effect.none(), []) })
      case validate(model) {
        Some(err) ->
          #(Model(..model, error: Some(err)), effect.none(), [])
        None -> {
          let request =
            models.RegisterRequest(email: model.email, password: model.password)
          let eff = {
            use dispatch <- effect.from
            auth.register(request)
            |> promise.tap(fn(result) { dispatch(RegisterResult(result)) })
            Nil
          }
          #(Model(..model, loading: True, error: None), eff, [])
        }
      }
    }

    RegisterResult(Ok(user)) ->
      #(Model(..model, loading: False), effect.none(), [
        shared.SetUser(user),
        shared.ShowSuccess(
          "Registration successful! Welcome to " <> shared.project_name <> ".",
        ),
        shared.Navigate(router.Home),
      ])

    RegisterResult(Error(err)) -> {
      let error_msg = case err {
        types.ValidationError(_) ->
          "Invalid registration data. Please check your inputs."
        types.AuthError(msg) -> msg
        types.StructuredError(_, _, _) -> "Username or email already exists."
        types.ServerError(409, _) -> "Username or email already exists."
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Registration failed. Please try again."
      }
      #(Model(..model, loading: False, error: Some(error_msg)), effect.none(), [])
    }
  }
}

// --- Helpers ---

fn validate(model: Model) -> Option(String) {
  case model.password == model.password_confirm {
    False -> Some("Passwords do not match")
    True ->
      case string.length(model.password) < 8 {
        True -> Some("Password must be at least 8 characters")
        False -> None
      }
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("register-page")], [
    html.div([attribute.class("register-container")], [
      html.div([attribute.class("register-card card")], [
        html.h1([attribute.class("register-title")], [
          html.text("Create Account"),
        ]),
        html.p([attribute.class("register-subtitle text-muted")], [
          html.text(
            "Register for "
            <> shared.project_name
            <> " "
            <> shared.project_description,
          ),
        ]),
        register_form(model),
        html.div([attribute.class("register-footer")], [
          html.p([attribute.class("text-muted")], [
            html.text("Already have an account? "),
            html.a(
              [attribute.href(router.route_to_path(router.Login))],
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
    [attribute.class("register-form"), event.on_submit(fn(_) { Submit })],
    [
      // Email field
      html.div([attribute.class("form-group")], [
        html.label(
          [attribute.for("reg-email"), attribute.class("form-label")],
          [html.text("Email")],
        ),
        html.input([
          attribute.class("form-input"),
          attribute.type_("email"),
          attribute.id("reg-email"),
          attribute.name("email"),
          attribute.placeholder("your.email@example.com"),
          attribute.value(model.email),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(UpdateEmail),
        ]),
        html.small([attribute.class("form-text text-muted")], [
          html.text("This will be your unique identifier for login"),
        ]),
      ]),
      // Password field
      html.div([attribute.class("form-group")], [
        html.label(
          [attribute.for("reg-password"), attribute.class("form-label")],
          [html.text("Password")],
        ),
        html.input([
          attribute.class("form-input"),
          attribute.type_("password"),
          attribute.id("reg-password"),
          attribute.name("password"),
          attribute.placeholder("Enter a strong password"),
          attribute.value(model.password),
          attribute.required(True),
          attribute.disabled(model.loading),
          attribute.attribute("minlength", "8"),
          event.on_input(UpdatePassword),
        ]),
        html.small([attribute.class("form-text text-muted")], [
          html.text("Minimum 8 characters"),
        ]),
      ]),
      // Password confirmation field
      html.div([attribute.class("form-group")], [
        html.label(
          [attribute.for("reg-password-confirm"), attribute.class("form-label")],
          [html.text("Confirm Password")],
        ),
        html.input([
          attribute.class("form-input"),
          attribute.type_("password"),
          attribute.id("reg-password-confirm"),
          attribute.name("password-confirm"),
          attribute.placeholder("Re-enter your password"),
          attribute.value(model.password_confirm),
          attribute.required(True),
          attribute.disabled(model.loading),
          event.on_input(UpdatePasswordConfirm),
        ]),
      ]),
      // Error message
      case model.error {
        Some(error) ->
          html.div([attribute.class("alert alert-danger")], [html.text(error)])
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
