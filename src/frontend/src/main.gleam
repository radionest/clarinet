// Main Lustre application
import api/auth
import api/models
import api/types
import components/layout
import gleam/javascript/promise
import gleam/option.{None, Some}
import gleam/uri.{type Uri}
import lustre
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import modem
import pages/home
import pages/login
import pages/register
import router.{type Route}
import store.{type Model, type Msg}
import utils/dom

// Initialize the application
pub fn main() {
  let app = lustre.application(init, update, view)
  let assert Ok(_) = lustre.start(app, "#app", Nil)
  Nil
}

// Initialize with routing
fn init(_flags) -> #(Model, Effect(Msg)) {
  let model = store.init()

  // Set up routing with modem
  let initial_route = case modem.initial_uri() {
    Ok(uri) -> router.parse_route(uri)
    Error(_) -> router.Home
  }

  let model_with_route = store.set_route(model, initial_route)

  #(model_with_route, modem.init(on_url_change))
}

// Handle URL changes from modem
fn on_url_change(uri: Uri) -> Msg {
  let route = router.parse_route(uri)
  store.OnRouteChange(route)
}

// Update function
pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  case msg {
    // Routing
    store.OnRouteChange(route) -> {
      let new_model = store.set_route(model, route)

      // Check authentication requirement
      case router.requires_auth(route), model.user {
        True, None -> {
          // Redirect to login if auth required
          #(store.set_route(model, router.Login), effect.none())
        }
        False, Some(_) if route == router.Login || route == router.Register -> {
          // Redirect from login/register if already authenticated
          #(
            store.set_route(model, router.Home),
            modem.push("/", option.None, option.None),
          )
        }
        _, _ -> {
          // Load data for the new route
          let effect = load_route_data(new_model, route)
          #(new_model, effect)
        }
      }
    }

    store.Navigate(route) -> {
      // Use Modem to update URL without page reload
      let path = router.route_to_path(route)
      #(model, modem.push(path, option.None, option.None))
    }

    // Authentication
    store.LoginSubmit(username, password) -> {
      let new_model = store.set_loading(model, True)
      let login_effect =
        effect.from(fn(dispatch) {
          auth.login(username, password)
          |> promise.tap(fn(result) {
            case result {
              Ok(response) -> {
                // Login response now only contains user data
                dispatch(store.LoginSuccess(response.user))
              }
              Error(error) -> dispatch(store.LoginError(error))
            }
          })
          Nil
        })
      #(new_model, login_effect)
    }

    store.LoginSuccess(user) -> {
      // Cookie authentication is handled automatically
      // Just update the model with the user
      let new_model =
        model
        |> store.set_user(user)
        |> store.set_loading(False)
        |> store.clear_messages()
        |> store.set_route(router.Home)

      #(new_model, modem.push("/", option.None, option.None))
    }

    store.LoginError(error) -> {
      let error_msg = case error {
        types.AuthError(msg) -> msg
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Login failed. Please try again."
      }

      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some(error_msg))

      #(new_model, effect.none())
    }

    store.RegisterSubmit(username, email, password) -> {
      let new_model =
        store.set_loading(model, True)
        |> store.clear_messages()

      let register_request =
        models.RegisterRequest(
          username: username,
          email: email,
          password: password,
          full_name: None,
        )

      let register_effect =
        effect.from(fn(dispatch) {
          auth.register(register_request)
          |> promise.tap(fn(result) {
            case result {
              Ok(user) -> dispatch(store.RegisterSuccess(user))
              Error(error) -> dispatch(store.RegisterError(error))
            }
          })
          Nil
        })
      #(new_model, register_effect)
    }

    store.RegisterSuccess(user) -> {
      // Registration successful - user is logged in via cookie
      let new_model =
        model
        |> store.set_user(user)
        |> store.set_loading(False)
        |> store.clear_messages()
        |> store.set_success("Registration successful! Welcome to Clarinet.")
        |> store.set_route(router.Home)

      #(new_model, modem.push("/", option.None, option.None))
    }

    store.RegisterError(error) -> {
      let error_msg = case error {
        types.ValidationError(_) ->
          "Invalid registration data. Please check your inputs."
        types.AuthError(msg) -> msg
        types.ServerError(409, _) -> "Username or email already exists."
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Registration failed. Please try again."
      }

      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some(error_msg))

      #(new_model, effect.none())
    }

    store.Logout -> {
      // Cookie will be cleared by server on logout
      let new_model =
        store.clear_user(model)
        |> store.set_route(router.Login)

      #(new_model, modem.push("/login", option.None, option.None))
    }

    // UI Messages
    store.ClearError -> {
      #(store.clear_messages(model), effect.none())
    }

    store.ClearSuccessMessage -> {
      #(store.clear_messages(model), effect.none())
    }

    // Default case
    _ -> #(model, effect.none())
  }
}

// View function
pub fn view(model: Model) -> Element(Msg) {
  let content = case model.route {
    router.Home -> home.view(model)
    router.Login -> login.view(model)
    router.Register -> register.view(model)
    router.Studies -> html.div([], [html.text("Studies page")])
    router.StudyDetail(_id) -> html.div([], [html.text("Study detail page")])
    router.Tasks -> html.div([], [html.text("Tasks page")])
    router.TaskDetail(_id) -> html.div([], [html.text("Task detail page")])
    router.TaskNew -> html.div([], [html.text("New task page")])
    router.TaskDesign(_id) -> html.div([], [html.text("Task design page")])
    router.Users -> html.div([], [html.text("Users page")])
    router.UserProfile(_id) -> html.div([], [html.text("User profile page")])
    router.NotFound -> html.div([], [html.text("404 - Page not found")])
  }

  case model.route {
    router.Login | router.Register -> content
    _ -> layout.view(model, content)
  }
}

// Load data for route
fn load_route_data(_model: Model, _route: Route) -> Effect(Msg) {
  // TODO: Implement data loading for each route
  effect.none()
}
