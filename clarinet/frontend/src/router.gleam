// Client-side routing with Modem
import config
import gleam/dict.{type Dict}
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/string
import gleam/uri.{type Uri}
import utils/record_filters

// Route definitions
pub type Route {
  Home
  Login
  Register
  Studies(filters: Dict(String, String))
  StudyDetail(id: String)
  StudyViewer(id: String)
  Records(filters: Dict(String, String))
  RecordDetail(id: String)
  RecordNew
  Patients(filters: Dict(String, String))
  PatientDetail(id: String)
  PatientNew
  SeriesDetail(id: String)
  AdminDashboard(filters: Dict(String, String))
  AdminRecordTypes
  AdminRecordTypeDetail(name: String)
  AdminRecordTypeEdit(name: String)
  AdminReports
  NotFound
}

// Convert Route to URL path (includes base path prefix for sub-path deployments)
pub fn route_to_path(route: Route) -> String {
  let base = config.base_path()
  let path = case route {
    Home -> "/"
    Login -> "/login"
    Register -> "/register"
    Studies(_) -> "/studies"
    StudyDetail(id) -> "/studies/" <> id
    StudyViewer(id) -> "/studies/" <> id <> "/viewer"
    Records(_) -> "/records"
    RecordDetail(id) -> "/records/" <> id
    RecordNew -> "/records/new"
    Patients(_) -> "/patients"
    PatientNew -> "/patients/new"
    PatientDetail(id) -> "/patients/" <> id
    SeriesDetail(id) -> "/series/" <> id
    AdminDashboard(_) -> "/admin"
    AdminRecordTypes -> "/admin/record-types"
    AdminRecordTypeDetail(name) -> "/admin/record-types/" <> name
    AdminRecordTypeEdit(name) -> "/admin/record-types/" <> name <> "/edit"
    AdminReports -> "/admin/reports"
    NotFound -> "/404"
  }
  base <> path
}

// Parse URL path to Route (strips base path prefix for sub-path deployments)
pub fn parse_route(uri: Uri) -> Route {
  let base = config.base_path()
  let raw_path = case base, string.starts_with(uri.path, base <> "/") {
    "", _ -> uri.path
    _, True -> string.drop_start(uri.path, up_to: string.length(base))
    _, False -> uri.path
  }
  let path =
    raw_path
    |> string.split("/")
    |> list.filter(fn(s) { string.length(s) > 0 })

  case path {
    [] -> Home
    ["login"] -> Login
    ["register"] -> Register
    ["studies"] -> Studies(parse_filters_from_query(uri.query))
    ["studies", id, "viewer"] -> StudyViewer(id)
    ["studies", id] -> StudyDetail(id)
    ["records"] -> Records(parse_filters_from_query(uri.query))
    ["records", "new"] -> RecordNew
    ["records", id] -> RecordDetail(id)
    ["patients"] -> Patients(parse_filters_from_query(uri.query))
    ["patients", "new"] -> PatientNew
    ["patients", id] -> PatientDetail(id)
    ["series", id] -> SeriesDetail(id)
    ["admin"] -> AdminDashboard(parse_filters_from_query(uri.query))
    ["admin", "reports"] -> AdminReports
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
    Studies(_)
    | StudyDetail(_)
    | SeriesDetail(_)
    | Patients(_)
    | PatientDetail(_)
    | PatientNew
    | RecordNew
    | AdminDashboard(_)
    | AdminRecordTypes
    | AdminRecordTypeDetail(_)
    | AdminRecordTypeEdit(_)
    | AdminReports -> True
    _ -> False
  }
}

// Get route title for display
pub fn get_route_title(route: Route) -> String {
  case route {
    Home -> "Dashboard"
    Login -> "Login"
    Register -> "Register"
    Studies(_) -> "Studies"
    StudyDetail(_) -> "Study Details"
    StudyViewer(_) -> "Study Viewer"
    Records(_) -> "Records"
    RecordDetail(_) -> "Record Details"
    RecordNew -> "New Record"
    Patients(_) -> "Patients"
    PatientDetail(_) -> "Patient Details"
    PatientNew -> "New Patient"
    SeriesDetail(_) -> "Series Details"
    AdminDashboard(_) -> "Admin Dashboard"
    AdminRecordTypes -> "Record Types"
    AdminRecordTypeDetail(_) -> "Record Type Details"
    AdminRecordTypeEdit(_) -> "Edit Record Type"
    AdminReports -> "Reports"
    NotFound -> "Page Not Found"
  }
}

fn section(route: Route) -> String {
  case route {
    Home -> "home"
    Login -> "login"
    Register -> "register"
    Studies(_) | StudyDetail(_) | StudyViewer(_) | SeriesDetail(_) -> "studies"
    Records(_) | RecordDetail(_) | RecordNew -> "records"
    Patients(_) | PatientDetail(_) | PatientNew -> "patients"
    AdminDashboard(_)
    | AdminRecordTypes
    | AdminRecordTypeDetail(_)
    | AdminRecordTypeEdit(_)
    | AdminReports -> "admin"
    NotFound -> "notfound"
  }
}

pub fn is_same_section(route1: Route, route2: Route) -> Bool {
  section(route1) == section(route2)
}

fn parse_filters_from_query(query: Option(String)) -> Dict(String, String) {
  case query {
    None -> dict.new()
    Some(qs) ->
      case uri.parse_query(qs) {
        Error(_) -> dict.new()
        Ok(pairs) ->
          pairs
          |> list.filter(fn(pair) {
            list.contains(record_filters.serializable_filter_keys, pair.0)
            && pair.1 != ""
          })
          |> dict.from_list
      }
  }
}

pub fn filters_to_query(filters: Dict(String, String)) -> Option(String) {
  let serializable = record_filters.keep_serializable(filters)
  case dict.is_empty(serializable) {
    True -> None
    False -> Some(uri.query_to_string(dict.to_list(serializable)))
  }
}

pub fn route_to_query(route: Route) -> Option(String) {
  case route {
    Records(filters)
    | Studies(filters)
    | Patients(filters)
    | AdminDashboard(filters) -> filters_to_query(filters)
    _ -> None
  }
}
