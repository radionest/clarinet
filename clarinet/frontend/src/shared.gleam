import api/info.{type ViewerInfo}
import api/models.{
  type Patient, type Record, type RecordType, type Series, type Study, type User,
}
import cache
import cache/bucket.{type BucketKey}
import clarinet_frontend/i18n.{type Key, type Locale}
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
    // Global entity caches (studies/series/records/record_types/patients/users/record_type_stats)
    cache: cache.Model,
    // Viewer plugins
    viewers: List(ViewerInfo),
    // i18n
    translate: fn(Key) -> String,
    locale: Locale,
  )
}

/// Context for opening the create-record modal from a detail page. The
/// constructor encodes which fields are auto-filled (and therefore
/// read-only) vs. user-selectable: deeper variants carry more locked
/// context. The user can still pick a parent strictly below the source
/// page level via the modal's cascading picker.
pub type OpenCreateRecordModalArgs {
  PatientArgs(patient_id: String)
  StudyArgs(patient_id: String, study_uid: String)
  SeriesArgs(patient_id: String, study_uid: String, series_uid: String)
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
  FetchBucket(BucketKey)
  FetchMoreBucket(BucketKey)
  InvalidateBucket(BucketKey)
  InvalidateAllRecordBuckets
  ReloadStudies
  ReloadUsers
  ReloadPatients
  ReloadRecordTypes
  ReloadRecordTypeStats
  ReloadPatient(String)
  ReloadRecord(String)
  ReloadSeries(series_uid: String)
  OpenDeleteConfirm(resource: String, id: String)
  OpenFailPrompt(record_id: String)
  OpenCreateRecordModal(args: OpenCreateRecordModalArgs)
  CloseRecordModal
  SetUser(User)
  Logout
  StartPreload(viewer_url: String, study_uid: String)
}
