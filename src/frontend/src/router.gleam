// Client-side routing with Modem
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import gleam/uri.{type Uri}

// Route definitions
pub type Route {
  Home
  Login
  Register
  Studies
  StudyDetail(id: String)
  Tasks
  TaskDetail(id: String)
  TaskNew
  TaskDesign(id: Option(String))
  Users
  UserProfile(id: String)
  NotFound
}

// Convert Route to URL path
pub fn route_to_path(route: Route) -> String {
  case route {
    Home -> "/"
    Login -> "/login"
    Register -> "/register"
    Studies -> "/studies"
    StudyDetail(id) -> "/studies/" <> id
    Tasks -> "/tasks"
    TaskDetail(id) -> "/tasks/" <> id
    TaskNew -> "/tasks/new"
    TaskDesign(None) -> "/tasks/design/new"
    TaskDesign(Some(id)) -> "/tasks/design/" <> id
    Users -> "/users"
    UserProfile(id) -> "/users/" <> id
    NotFound -> "/404"
  }
}

// Parse URL path to Route
pub fn parse_route(uri: Uri) -> Route {
  let path =
    uri.path
    |> string.split("/")
    |> list.filter(fn(s) { string.length(s) > 0 })

  case path {
    [] -> Home
    ["login"] -> Login
    ["register"] -> Register
    ["studies"] -> Studies
    ["studies", id] -> StudyDetail(id)
    ["tasks"] -> Tasks
    ["tasks", "new"] -> TaskNew
    ["tasks", "design", "new"] -> TaskDesign(None)
    ["tasks", "design", id] -> TaskDesign(Some(id))
    ["tasks", id] -> TaskDetail(id)
    ["users"] -> Users
    ["users", id] -> UserProfile(id)
    _ -> NotFound
  }
}

// Check if route requires authentication
pub fn requires_auth(route: Route) -> Bool {
  case route {
    Login -> False
    Register -> False
    _ -> True
  }
}

// Get route title for display
pub fn get_route_title(route: Route) -> String {
  case route {
    Home -> "Dashboard"
    Login -> "Login"
    Register -> "Register"
    Studies -> "Studies"
    StudyDetail(_) -> "Study Details"
    Tasks -> "Tasks"
    TaskDetail(_) -> "Task Details"
    TaskNew -> "New Task"
    TaskDesign(None) -> "New Task Design"
    TaskDesign(Some(_)) -> "Edit Task Design"
    Users -> "Users"
    UserProfile(_) -> "User Profile"
    NotFound -> "Page Not Found"
  }
}

// Check if routes are in the same section
pub fn is_same_section(route1: Route, route2: Route) -> Bool {
  case route1, route2 {
    Studies, StudyDetail(_) -> True
    StudyDetail(_), Studies -> True
    StudyDetail(_), StudyDetail(_) -> True

    Tasks, TaskDetail(_) -> True
    Tasks, TaskNew -> True
    Tasks, TaskDesign(_) -> True
    TaskDetail(_), Tasks -> True
    TaskNew, Tasks -> True
    TaskDesign(_), Tasks -> True

    Users, UserProfile(_) -> True
    UserProfile(_), Users -> True
    UserProfile(_), UserProfile(_) -> True

    _, _ -> route1 == route2
  }
}
