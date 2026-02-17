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
  Records
  RecordDetail(id: String)
  RecordNew
  RecordTypeDesign(id: Option(String))
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
    Records -> "/records"
    RecordDetail(id) -> "/records/" <> id
    RecordNew -> "/records/new"
    RecordTypeDesign(None) -> "/records/type/new"
    RecordTypeDesign(Some(id)) -> "/records/type/" <> id
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
    ["records"] -> Records
    ["records", "new"] -> RecordNew
    ["records", "type", "new"] -> RecordTypeDesign(None)
    ["records", "type", id] -> RecordTypeDesign(Some(id))
    ["records", id] -> RecordDetail(id)
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
    Records -> "Records"
    RecordDetail(_) -> "Record Details"
    RecordNew -> "New Record"
    RecordTypeDesign(None) -> "New Record Type"
    RecordTypeDesign(Some(_)) -> "Edit Record Type"
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

    Records, RecordDetail(_) -> True
    Records, RecordNew -> True
    Records, RecordTypeDesign(_) -> True
    RecordDetail(_), Records -> True
    RecordNew, Records -> True
    RecordTypeDesign(_), Records -> True

    Users, UserProfile(_) -> True
    UserProfile(_), Users -> True
    UserProfile(_), UserProfile(_) -> True

    _, _ -> route1 == route2
  }
}
