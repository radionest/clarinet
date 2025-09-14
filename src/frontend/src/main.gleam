// Main Lustre application
import lustre
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import lustre/effect.{type Effect}
import gleam/uri
import gleam/option.{None, Some}
import modem
import router.{type Route}
import store.{type Model, type Msg}
import api/auth
import components/layout
import pages/login
import pages/home

// Initialize the application
pub fn main() {
  let app = lustre.application(init, update, view)
  let assert Ok(_) = lustre.start(app, "#app", Nil)
  Nil
}

// Initialize application state and effects
fn init(_flags) -> #(Model, Effect(Msg)) {
  let model = store.init()

  // Check for stored token
  let model = case auth.get_stored_token() {
    Some(token) -> {
      // TODO: Validate token and load user
      store.Model(..model, token: Some(token))
    }
    None -> model
  }

  // Initialize router
  let router_effect = modem.init(on_route_change)

  #(model, router_effect)
}

// Handle route changes
fn on_route_change(uri: uri.Uri) -> Msg {
  let route = router.parse_route(uri)
  store.OnRouteChange(route)
}

// Update function - handles all state changes
fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  case msg {
    // Navigation
    store.OnRouteChange(route) -> {
      let new_model = store.set_route(model, route)

      // Check authentication requirement
      case router.requires_auth(route), model.token {
        True, None -> {
          // Redirect to login if auth required
          #(store.set_route(model, router.Login), modem.push("/login"))
        }
        False, Some(_) if route == router.Login -> {
          // Redirect from login if already authenticated
          #(new_model, modem.push("/"))
        }
        _, _ -> {
          // Load data for the new route
          let effect = load_route_data(new_model, route)
          #(new_model, effect)
        }
      }
    }

    store.Navigate(route) -> {
      #(model, modem.push(router.route_to_path(route)))
    }

    // Authentication
    store.LoginSubmit(username, password) -> {
      let new_model = store.set_loading(model, True)
      let login_effect = auth.login(model.api_config, username, password)
        |> effect.from_promise(fn(result) {
          case result {
            Ok(response) -> store.LoginSuccess(response.access_token, response.user)
            Error(error) -> store.LoginError(error)
          }
        })
      #(new_model, login_effect)
    }

    store.LoginSuccess(token, user) -> {
      // Store token
      auth.store_token(token)

      let new_model = model
        |> store.set_auth(token, user)
        |> store.set_loading(False)
        |> store.set_success("Login successful")

      #(new_model, modem.push("/"))
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
      #(new_model, modem.push("/login"))
    }

    // UI Messages
    store.SetLoading(loading) -> {
      #(store.set_loading(model, loading), effect.none())
    }

    store.SetError(error) -> {
      #(store.set_error(model, error), effect.none())
    }

    store.ClearError -> {
      #(store.set_error(model, None), effect.none())
    }

    store.SetSuccessMessage(message) -> {
      let new_model = store.set_success(model, message)
      // Auto-clear success message after 3 seconds
      let clear_effect = effect.from(fn(dispatch) {
        set_timeout(3000, fn() { dispatch(store.ClearSuccessMessage) })
        Nil
      })
      #(new_model, clear_effect)
    }

    store.ClearSuccessMessage -> {
      #(store.Model(..model, success_message: None), effect.none())
    }

    // Default case for unhandled messages
    _ -> #(model, effect.none())
  }
}

// Load data based on current route
fn load_route_data(model: Model, route: Route) -> Effect(Msg) {
  case route {
    router.Studies -> effect.from(fn(dispatch) {
      dispatch(store.LoadStudies)
      Nil
    })

    router.StudyDetail(id) -> effect.from(fn(dispatch) {
      dispatch(store.LoadStudyDetail(id))
      Nil
    })

    router.Tasks -> effect.from(fn(dispatch) {
      dispatch(store.LoadTasks)
      Nil
    })

    router.TaskDetail(id) -> effect.from(fn(dispatch) {
      dispatch(store.LoadTaskDetail(id))
      Nil
    })

    router.Users if is_admin(model) -> effect.from(fn(dispatch) {
      dispatch(store.LoadUsers)
      Nil
    })

    _ -> effect.none()
  }
}

// Check if current user is admin
fn is_admin(model: Model) -> Bool {
  case model.user {
    Some(user) -> user.role == models.Admin
    None -> False
  }
}

// Main view function
fn view(model: Model) -> Element(Msg) {
  case model.user {
    Some(_) -> layout.view(model, page_content(model))
    None -> {
      // Show login page without layout for unauthenticated users
      case model.route {
        router.Login -> login.view(model)
        _ -> login.view(model)
      }
    }
  }
}

// Render page content based on route
fn page_content(model: Model) -> Element(Msg) {
  case model.route {
    router.Home -> home.view(model)
    router.Login -> login.view(model)
    router.Studies -> pages.studies.list.view(model)
    router.StudyDetail(id) -> pages.studies.detail.view(model, id)
    router.Tasks -> pages.tasks.list.view(model)
    router.TaskDetail(id) -> pages.tasks.detail.view(model, id)
    router.TaskNew -> pages.tasks.new.view(model)
    router.TaskDesign(id) -> pages.tasks.design.view(model, id)
    router.Users -> pages.users.list.view(model)
    router.UserProfile(id) -> pages.users.profile.view(model, id)
    router.NotFound -> not_found_view()
  }
}

// 404 page
fn not_found_view() -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("404 - Page Not Found")]),
    html.p([], [html.text("The page you're looking for doesn't exist.")]),
    html.a(
      [
        attribute.href("/"),
        attribute.class("btn btn-primary"),
      ],
      [html.text("Go Home")]
    ),
  ])
}

// JavaScript FFI for setTimeout
@external(javascript, "./ffi/utils.js", "setTimeout")
fn set_timeout(delay: Int, callback: fn() -> Nil) -> Nil