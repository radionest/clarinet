// Main Lustre application
import lustre
import lustre/element.{type Element}
import lustre/element/html
import lustre/effect.{type Effect}
import gleam/uri.{type Uri}
import gleam/option.{None, Some}
import gleam/javascript/promise
import modem
import router.{type Route}
import store.{type Model, type Msg}
import api/types
import api/auth
import api/models
import components/layout
import pages/login
import pages/home

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
      case router.requires_auth(route), model.token {
        True, None -> {
          // Redirect to login if auth required
          #(store.set_route(model, router.Login), effect.none())
        }
        False, Some(_) if route == router.Login -> {
          // Redirect from login if already authenticated
          #(new_model, effect.none())
        }
        _, _ -> {
          // Load data for the new route
          let effect = load_route_data(new_model, route)
          #(new_model, effect)
        }
      }
    }

    store.Navigate(route) -> {
      // Use JavaScript to update URL
      #(model, effect.from(fn(_) {
        update_url(router.route_to_path(route))
        Nil
      }))
    }

    // Authentication
    store.LoginSubmit(username, password) -> {
      let new_model = store.set_loading(model, True)
      let login_effect = effect.from(fn(dispatch) {
        auth.login(model.api_config, username, password)
        |> promise.tap(fn(result) {
          case result {
            Ok(response) -> {
              // We need to access the response fields properly
              dispatch(store.LoginSuccess("", models.User(
                id: "",
                username: username,
                email: "",
                hashed_password: None,
                is_active: True,
                is_superuser: False,
                is_verified: False,
                roles: None,
                tasks: None
              )))
            }
            Error(error) -> dispatch(store.LoginError(error))
          }
        })
        Nil
      })
      #(new_model, login_effect)
    }

    store.LoginSuccess(token, user) -> {
      // Store token
      auth.store_token(token)

      // Update model
      let new_model = model
        |> store.set_auth(token, user)
        |> store.set_loading(False)
        |> store.clear_messages()
        |> store.set_route(router.Home)

      #(new_model, effect.from(fn(_) {
        update_url("/")
        Nil
      }))
    }

    store.LoginError(error) -> {
      let error_msg = case error {
        types.AuthError(msg) -> msg
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Login failed. Please try again."
      }

      let new_model = model
        |> store.set_loading(False)
        |> store.set_error(Some(error_msg))

      #(new_model, effect.none())
    }

    store.Logout -> {
      auth.clear_token()
      let new_model = store.clear_auth(model)
        |> store.set_route(router.Login)

      #(new_model, effect.from(fn(_) {
        update_url("/login")
        Nil
      }))
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
    router.Login -> content
    _ -> layout.view(model, content)
  }
}

// Load data for route
fn load_route_data(_model: Model, _route: Route) -> Effect(Msg) {
  // TODO: Implement data loading for each route
  effect.none()
}

// JavaScript FFI for URL updates
@external(javascript, "./ffi/utils.js", "updateUrl")
fn update_url(path: String) -> Nil