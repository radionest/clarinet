// Main Lustre application
import api/admin
import api/auth
import api/models
import api/records
import api/studies
import api/types
import api/users
import components/layout
import gleam/dict
import gleam/int
import gleam/io
import gleam/javascript/promise
import gleam/string
import gleam/list
import gleam/option.{None, Some}
import gleam/uri.{type Uri}
import lustre
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import modem
import pages/admin as admin_page
import pages/home
import pages/login
import pages/register
import router.{type Route}
import store.{type Model, type Msg}

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

  // Check existing session via cookie
  let check_session_effect =
    effect.from(fn(dispatch) {
      auth.get_current_user()
      |> promise.tap(fn(result) { dispatch(store.CheckSessionResult(result)) })
      Nil
    })

  #(
    model_with_route,
    effect.batch([modem.init(on_url_change), check_session_effect]),
  )
}

// Handle URL changes from modem
fn on_url_change(uri: Uri) -> Msg {
  io.println(">>> on_url_change fired, uri: " <> string.inspect(uri))
  let route = router.parse_route(uri)
  io.println(">>> parsed route: " <> string.inspect(route))
  store.OnRouteChange(route)
}

// Update function
pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  case msg {
    // Routing
    store.OnRouteChange(route) -> {
      io.println(
        ">>> OnRouteChange handler, route: " <> string.inspect(route)
        <> ", checking_session: " <> string.inspect(model.checking_session)
        <> ", user: " <> string.inspect(model.user),
      )
      let new_model = store.set_route(model, route)

      // Don't redirect while session check is in progress
      case model.checking_session {
        True -> #(new_model, effect.none())
        False -> {
          // Check authentication requirement
          let is_auth_page = route == router.Login || route == router.Register
          case router.requires_auth(route), model.user, is_auth_page {
            True, None, _ -> {
              // Redirect to login if auth required
              #(
                store.set_route(model, router.Login),
                modem.push("/login", option.None, option.None),
              )
            }
            False, Some(_), True -> {
              // Redirect from login/register if already authenticated
              #(
                store.set_route(model, router.Home),
                modem.push("/", option.None, option.None),
              )
            }
            _, _, _ -> {
              // Load data for the new route
              let effect = load_route_data(new_model, route)
              #(new_model, effect)
            }
          }
        }
      }
    }

    store.Navigate(route) -> {
      io.println(">>> Navigate handler, route: " <> string.inspect(route))
      // Use Modem to update URL without page reload
      let path = router.route_to_path(route)
      io.println(">>> Navigate pushing path: " <> path)
      #(model, modem.push(path, option.None, option.None))
    }

    // Session restoration
    store.CheckSessionResult(result) -> {
      case result {
        Ok(user) -> {
          // Session is valid - restore user and load route data
          let new_model =
            model
            |> store.set_user(user)
            |> fn(m) { store.Model(..m, checking_session: False) }
          let route = model.route
          let is_auth_page = route == router.Login || route == router.Register
          case router.requires_auth(route), new_model.user, is_auth_page {
            False, Some(_), True ->
              #(
                store.set_route(new_model, router.Home),
                modem.push("/", option.None, option.None),
              )
            _, _, _ -> #(new_model, load_route_data(new_model, route))
          }
        }
        Error(_) -> {
          // No valid session - redirect to login if on protected route
          let new_model = store.Model(..model, checking_session: False)
          case router.requires_auth(model.route) {
            True ->
              #(
                store.set_route(new_model, router.Login),
                modem.push("/login", option.None, option.None),
              )
            False -> #(new_model, effect.none())
          }
        }
      }
    }

    // Auth form updates
    store.LoginUpdateEmail(value) -> {
      #(store.Model(..model, login_email: value), effect.none())
    }

    store.LoginUpdatePassword(value) -> {
      #(store.Model(..model, login_password: value), effect.none())
    }

    store.RegisterUpdateEmail(value) -> {
      #(store.Model(..model, register_email: value), effect.none())
    }

    store.RegisterUpdatePassword(value) -> {
      #(store.Model(..model, register_password: value), effect.none())
    }

    store.RegisterUpdatePasswordConfirm(value) -> {
      #(store.Model(..model, register_password_confirm: value), effect.none())
    }

    // Authentication
    store.LoginSubmit(email, password) -> {
      let new_model = store.set_loading(model, True)
      let login_effect =
        effect.from(fn(dispatch) {
          auth.login(email, password)
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
        |> store.clear_auth_forms()
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

    store.RegisterSubmit(email, password) -> {
      let new_model =
        store.set_loading(model, True)
        |> store.clear_messages()

      let register_request =
        models.RegisterRequest(
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
        |> store.clear_auth_forms()
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
      let logout_effect =
        effect.from(fn(dispatch) {
          auth.logout()
          |> promise.tap(fn(_) { dispatch(store.LogoutComplete) })
          Nil
        })
      #(store.clear_user(model), logout_effect)
    }

    store.LogoutComplete -> {
      #(
        store.set_route(model, router.Login),
        modem.push("/login", option.None, option.None),
      )
    }

    // UI Messages
    store.ClearError -> {
      #(store.clear_messages(model), effect.none())
    }

    store.ClearSuccessMessage -> {
      #(store.clear_messages(model), effect.none())
    }

    // Data loading - Studies
    store.LoadStudies -> {
      let load_effect =
        effect.from(fn(dispatch) {
          studies.get_studies()
          |> promise.tap(fn(result) { dispatch(store.StudiesLoaded(result)) })
          Nil
        })
      #(store.set_loading(model, True), load_effect)
    }

    store.StudiesLoaded(Ok(studies_list)) -> {
      let studies_dict =
        list.fold(studies_list, model.studies, fn(acc, study) {
          dict.insert(acc, study.study_uid, study)
        })
      let new_model =
        store.Model(..model, studies: studies_dict, studies_list: studies_list, loading: False)
      #(new_model, effect.none())
    }

    store.StudiesLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load studies"))
      #(new_model, effect.none())
    }

    // Data loading - Records
    store.LoadRecords -> {
      let load_effect =
        effect.from(fn(dispatch) {
          records.get_records()
          |> promise.tap(fn(result) { dispatch(store.RecordsLoaded(result)) })
          Nil
        })
      #(store.set_loading(model, True), load_effect)
    }

    store.RecordsLoaded(Ok(records_list)) -> {
      let records_dict =
        list.fold(records_list, model.records, fn(acc, record) {
          case record.id {
            Some(id) -> dict.insert(acc, int.to_string(id), record)
            None -> acc
          }
        })
      let new_model =
        store.Model(..model, records: records_dict, records_list: records_list, loading: False)
      #(new_model, effect.none())
    }

    store.RecordsLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load records"))
      #(new_model, effect.none())
    }

    // Data loading - Users
    store.LoadUsers -> {
      let load_effect =
        effect.from(fn(dispatch) {
          users.get_users()
          |> promise.tap(fn(result) { dispatch(store.UsersLoaded(result)) })
          Nil
        })
      #(store.set_loading(model, True), load_effect)
    }

    store.UsersLoaded(Ok(users_list)) -> {
      let users_dict =
        list.fold(users_list, model.users, fn(acc, user) {
          dict.insert(acc, user.id, user)
        })
      let new_model =
        store.Model(..model, users: users_dict, users_list: users_list, loading: False)
      #(new_model, effect.none())
    }

    store.UsersLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load users"))
      #(new_model, effect.none())
    }

    // Data loading - Admin Stats
    store.LoadAdminStats -> {
      let load_effect =
        effect.from(fn(dispatch) {
          admin.get_admin_stats()
          |> promise.tap(fn(result) { dispatch(store.AdminStatsLoaded(result)) })
          Nil
        })
      #(store.set_loading(model, True), load_effect)
    }

    store.AdminStatsLoaded(Ok(stats)) -> {
      let new_model =
        store.Model(..model, admin_stats: Some(stats), loading: False)
      #(new_model, effect.none())
    }

    store.AdminStatsLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load admin statistics"))
      #(new_model, effect.none())
    }

    // Admin record assignment
    store.AdminToggleAssignDropdown(record_id) -> {
      #(store.Model(..model, admin_editing_record_id: record_id), effect.none())
    }

    store.AdminAssignUser(record_id, user_id) -> {
      let new_model =
        store.Model(..model, admin_editing_record_id: None, loading: True)
      let assign_effect =
        effect.from(fn(dispatch) {
          admin.assign_record_user(record_id, user_id)
          |> promise.tap(fn(result) {
            dispatch(store.AdminAssignUserResult(result))
          })
          Nil
        })
      #(new_model, assign_effect)
    }

    store.AdminAssignUserResult(Ok(record)) -> {
      let new_model =
        model
        |> store.update_record_in_list(record)
        |> store.set_loading(False)
        |> store.set_success("User assigned successfully")
      #(new_model, effect.none())
    }

    store.AdminAssignUserResult(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to assign user to record"))
      #(new_model, effect.none())
    }

    // Default case
    _ -> #(model, effect.none())
  }
}

// View function
pub fn view(model: Model) -> Element(Msg) {
  // Show loading while checking session
  case model.checking_session {
    True -> html.div([], [])
    False -> view_content(model)
  }
}

fn view_content(model: Model) -> Element(Msg) {
  let content = case model.route {
    router.Home -> home.view(model)
    router.Login -> login.view(model)
    router.Register -> register.view(model)
    router.Studies -> html.div([], [html.text("Studies page")])
    router.StudyDetail(_id) -> html.div([], [html.text("Study detail page")])
    router.Records -> html.div([], [html.text("Records page")])
    router.RecordDetail(_id) -> html.div([], [html.text("Record detail page")])
    router.RecordNew -> html.div([], [html.text("New record page")])
    router.RecordTypeDesign(_id) -> html.div([], [html.text("Record type design page")])
    router.Users -> html.div([], [html.text("Users page")])
    router.UserProfile(_id) -> html.div([], [html.text("User profile page")])
    router.AdminDashboard -> admin_page.view(model)
    router.NotFound -> html.div([], [html.text("404 - Page not found")])
  }

  case model.route {
    router.Login | router.Register -> content
    _ -> layout.view(model, content)
  }
}

// Load data for route
fn load_route_data(model: Model, route: Route) -> Effect(Msg) {
  case route, model.user {
    router.Home, Some(_) ->
      effect.batch([
        effect.from(fn(dispatch) { dispatch(store.LoadStudies) }),
        effect.from(fn(dispatch) { dispatch(store.LoadRecords) }),
        effect.from(fn(dispatch) { dispatch(store.LoadUsers) }),
      ])
    router.Home, None -> effect.none()
    router.Studies, _ -> effect.from(fn(dispatch) { dispatch(store.LoadStudies) })
    router.Records, _ -> effect.from(fn(dispatch) { dispatch(store.LoadRecords) })
    router.Users, _ -> effect.from(fn(dispatch) { dispatch(store.LoadUsers) })
    router.AdminDashboard, Some(_) ->
      effect.batch([
        effect.from(fn(dispatch) { dispatch(store.LoadAdminStats) }),
        effect.from(fn(dispatch) { dispatch(store.LoadRecords) }),
        effect.from(fn(dispatch) { dispatch(store.LoadUsers) }),
      ])
    _, _ -> effect.none()
  }
}
