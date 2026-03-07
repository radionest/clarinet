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
  StudyViewer(id: String)
  Records
  RecordDetail(id: String)
  RecordNew
  RecordTypeDesign(id: Option(String))
  Patients
  PatientDetail(id: String)
  PatientNew
  SeriesDetail(id: String)
  Users
  UserProfile(id: String)
  AdminDashboard
  AdminRecordTypes
  AdminRecordTypeDetail(name: String)
  AdminRecordTypeEdit(name: String)
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
    StudyViewer(id) -> "/studies/" <> id <> "/viewer"
    Records -> "/records"
    RecordDetail(id) -> "/records/" <> id
    RecordNew -> "/records/new"
    RecordTypeDesign(None) -> "/records/type/new"
    RecordTypeDesign(Some(id)) -> "/records/type/" <> id
    Patients -> "/patients"
    PatientNew -> "/patients/new"
    PatientDetail(id) -> "/patients/" <> id
    SeriesDetail(id) -> "/series/" <> id
    Users -> "/users"
    UserProfile(id) -> "/users/" <> id
    AdminDashboard -> "/admin"
    AdminRecordTypes -> "/admin/record-types"
    AdminRecordTypeDetail(name) -> "/admin/record-types/" <> name
    AdminRecordTypeEdit(name) -> "/admin/record-types/" <> name <> "/edit"
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
    ["studies", id, "viewer"] -> StudyViewer(id)
    ["studies", id] -> StudyDetail(id)
    ["records"] -> Records
    ["records", "new"] -> RecordNew
    ["records", "type", "new"] -> RecordTypeDesign(None)
    ["records", "type", id] -> RecordTypeDesign(Some(id))
    ["records", id] -> RecordDetail(id)
    ["patients"] -> Patients
    ["patients", "new"] -> PatientNew
    ["patients", id] -> PatientDetail(id)
    ["series", id] -> SeriesDetail(id)
    ["users"] -> Users
    ["users", id] -> UserProfile(id)
    ["admin"] -> AdminDashboard
    ["admin", "record-types"] -> AdminRecordTypes
    ["admin", "record-types", name, "edit"] -> AdminRecordTypeEdit(name)
    ["admin", "record-types", name] -> AdminRecordTypeDetail(name)
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

// Check if route requires admin role
pub fn requires_admin_role(route: Route) -> Bool {
  case route {
    Studies
    | StudyDetail(_)
    | SeriesDetail(_)
    | Patients
    | PatientDetail(_)
    | PatientNew
    | Users
    | UserProfile(_)
    | AdminDashboard
    | AdminRecordTypes
    | AdminRecordTypeDetail(_)
    | AdminRecordTypeEdit(_) -> True
    _ -> False
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
    StudyViewer(_) -> "Study Viewer"
    Records -> "Records"
    RecordDetail(_) -> "Record Details"
    RecordNew -> "New Record"
    RecordTypeDesign(None) -> "New Record Type"
    RecordTypeDesign(Some(_)) -> "Edit Record Type"
    Patients -> "Patients"
    PatientDetail(_) -> "Patient Details"
    PatientNew -> "New Patient"
    SeriesDetail(_) -> "Series Details"
    Users -> "Users"
    UserProfile(_) -> "User Profile"
    AdminDashboard -> "Admin Dashboard"
    AdminRecordTypes -> "Record Types"
    AdminRecordTypeDetail(_) -> "Record Type Details"
    AdminRecordTypeEdit(_) -> "Edit Record Type"
    NotFound -> "Page Not Found"
  }
}

fn section(route: Route) -> String {
  case route {
    Home -> "home"
    Login -> "login"
    Register -> "register"
    Studies | StudyDetail(_) | StudyViewer(_) | SeriesDetail(_) -> "studies"
    Records | RecordDetail(_) | RecordNew | RecordTypeDesign(_) -> "records"
    Patients | PatientDetail(_) | PatientNew -> "patients"
    Users | UserProfile(_) -> "users"
    AdminDashboard
    | AdminRecordTypes
    | AdminRecordTypeDetail(_)
    | AdminRecordTypeEdit(_) -> "admin"
    NotFound -> "notfound"
  }
}

pub fn is_same_section(route1: Route, route2: Route) -> Bool {
  section(route1) == section(route2)
}
