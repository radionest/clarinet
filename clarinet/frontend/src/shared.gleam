import api/models.{
  type Patient, type Record, type RecordType, type Series, type Study, type User,
}
import gleam/dict.{type Dict}
import gleam/option.{type Option}
import router.{type Route}

/// Read-only context passed from main to page modules.
/// Pages use this to access global state without depending on store.Model.
pub type Shared {
  Shared(
    user: Option(User),
    route: Route,
    project_name: String,
    project_description: String,
    // Global caches
    studies: Dict(String, Study),
    series: Dict(String, Series),
    records: Dict(String, Record),
    record_types: Dict(String, RecordType),
    patients: Dict(String, Patient),
    users: Dict(String, User),
  )
}

/// Commands from page modules back to main.
/// Pages return List(OutMsg) from their update function
/// to request global state changes.
pub type OutMsg {
  ShowSuccess(String)
  ShowError(String)
  Navigate(Route)
  SetLoading(Bool)
  CacheRecord(Record)
  CacheStudy(Study)
  CachePatient(Patient)
  CacheRecordType(RecordType)
  CacheSeries(Series)
  ReloadRecords
  ReloadStudies
  ReloadUsers
  ReloadPatients
  ReloadRecordTypes
  ReloadPatient(String)
  ReloadRecord(String)
  OpenDeleteConfirm(resource: String, id: String)
  SetUser(User)
  Logout
  StartPreload(viewer_url: String, study_uid: String)
}
