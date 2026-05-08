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

/// Identifies which detail page is opening the create-record modal.
/// Determines which fields are auto-filled (read-only) vs. user-selectable
/// based on the DICOM hierarchy: a page at level X knows everything from
/// X up to Patient and lets the user pick anything strictly below X.
pub type RecordPageLevel {
  PatientLevel
  StudyLevel
  SeriesLevel
}

/// Context for opening the create-record modal from a detail page.
/// `patient_id` is always known. `study_uid` / `series_uid` are filled when
/// the source page is at that level or deeper — those become read-only.
/// `None` slots stay editable so the user can pick a parent if the chosen
/// RecordType.level is below the source page level.
pub type OpenCreateRecordModalArgs {
  OpenCreateRecordModalArgs(
    page_level: RecordPageLevel,
    patient_id: String,
    study_uid: Option(String),
    series_uid: Option(String),
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
