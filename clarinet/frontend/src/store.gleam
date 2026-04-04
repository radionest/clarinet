// Global state management
import api/info.{type ProjectInfo}
import api/models.{
  type Patient, type RecordTypeStats,
  type Series, type Study, type Record, type RecordType, type User,
}
import pages/admin as admin_page
import pages/login as login_page
import pages/patients/detail as patient_detail_page
import pages/patients/list as patients_list_page
import pages/patients/new as patient_new_page
import pages/records/execute as record_execute_page
import pages/records/list as records_list_page
import pages/record_types/detail as record_type_detail_page
import pages/record_types/edit as record_type_edit_page
import pages/record_types/list as record_types_list_page
import pages/records/new as record_new_page
import pages/register as register_page
import pages/series/detail as series_detail_page
import pages/home as home_page
import pages/studies/detail as study_detail_page
import pages/studies/list as studies_list_page
import api/types.{type ApiError}
import gleam/dict.{type Dict}
import gleam/int
import gleam/option.{type Option, None, Some}
import preload
import router.{type Route}

// Application state model
pub type Model {
  Model(
    // Navigation
    route: Route,
    // Authentication (using cookie-based auth)
    user: Option(User),
    checking_session: Bool,
    // Project branding
    project_name: String,
    project_description: String,
    // UI State
    loading: Bool,
    error: Option(String),
    success_message: Option(String),
    // Data caches
    studies: Dict(String, Study),
    series: Dict(String, Series),
    records: Dict(String, Record),
    record_types: Dict(String, RecordType),
    patients: Dict(String, Patient),
    users: Dict(String, User),
    // Admin
    record_type_stats: Option(List(RecordTypeStats)),
    // Modal state
    modal_open: Bool,
    modal_content: ModalContent,
    // Preload
    preload: preload.Model,
    // Active page model (for modular pages)
    page: PageModel,
  )
}

// Active page model (for modular pages)
pub type PageModel {
  NoPage
  AdminPage(admin_page.Model)
  LoginPage(login_page.Model)
  RegisterPage(register_page.Model)
  PatientsListPage(patients_list_page.Model)
  PatientDetailPage(patient_detail_page.Model)
  PatientNewPage(patient_new_page.Model)
  RecordsListPage(records_list_page.Model)
  RecordExecutePage(record_execute_page.Model)
  RecordNewPage(record_new_page.Model)
  StudiesListPage(studies_list_page.Model)
  StudyDetailPage(study_detail_page.Model)
  SeriesDetailPage(series_detail_page.Model)
  RecordTypesListPage(record_types_list_page.Model)
  RecordTypeDetailPage(record_type_detail_page.Model)
  RecordTypeEditPage(record_type_edit_page.Model)
  HomePage(home_page.Model)
}

// Modal content types
pub type ModalContent {
  NoModal
  ConfirmDelete(resource: String, id: String)
}

// Application messages
pub type Msg {
  // Navigation
  OnRouteChange(Route)
  Navigate(Route)

  // Authentication
  CheckSessionResult(Result(User, ApiError))
  Logout
  LogoutComplete

  // Auth page delegation
  LoginMsg(login_page.Msg)
  RegisterMsg(register_page.Msg)

  // Patient page delegation
  PatientsListMsg(patients_list_page.Msg)
  PatientDetailMsg(patient_detail_page.Msg)
  PatientNewMsg(patient_new_page.Msg)

  // Record page delegation
  RecordsListMsg(records_list_page.Msg)
  RecordExecuteMsg(record_execute_page.Msg)
  RecordNewMsg(record_new_page.Msg)

  // Study/Series page delegation
  StudiesListMsg(studies_list_page.Msg)
  StudyDetailMsg(study_detail_page.Msg)
  SeriesDetailMsg(series_detail_page.Msg)

  // Record type page delegation
  RecordTypesListMsg(record_types_list_page.Msg)
  RecordTypeDetailMsg(record_type_detail_page.Msg)
  RecordTypeEditMsg(record_type_edit_page.Msg)

  // Home page delegation
  HomeMsg(home_page.Msg)

  // Data loading
  LoadStudies
  StudiesLoaded(Result(List(Study), ApiError))

  LoadRecords
  RecordsLoaded(Result(List(Record), ApiError))
  LoadRecordDetail(id: String)
  RecordDetailLoaded(Result(Record, ApiError))

  LoadUsers
  UsersLoaded(Result(List(User), ApiError))

  LoadPatients
  PatientsLoaded(Result(List(Patient), ApiError))
  LoadPatientDetail(id: String)
  PatientDetailLoaded(Result(Patient, ApiError))

  LoadRecordTypeStats
  RecordTypeStatsLoaded(Result(List(RecordTypeStats), ApiError))

  // Admin page delegation
  AdminMsg(admin_page.Msg)

  // Record types loading (used by record_new page init + admin)
  LoadRecordTypes
  RecordTypesLoaded(Result(List(RecordType), ApiError))

  // UI Actions
  SetError(Option(String))
  ClearError
  ClearSuccessMessage

  OpenModal(ModalContent)
  CloseModal
  ConfirmModalAction

  // Project info
  ProjectInfoLoaded(Result(ProjectInfo, ApiError))

  // Auto-assign
  AutoAssignResult(Result(Record, ApiError))

  // Preload delegation
  PreloadMsg(preload.Msg)
}

// Initialize application state
pub fn init() -> Model {
  Model(
    route: router.Home,
    user: None,
    checking_session: True,
    project_name: "Clarinet",
    project_description: "Medical Imaging Framework",
    loading: False,
    error: None,
    success_message: None,
    studies: dict.new(),
    series: dict.new(),
    records: dict.new(),
    record_types: dict.new(),
    patients: dict.new(),
    users: dict.new(),
    record_type_stats: None,
    modal_open: False,
    modal_content: NoModal,
    preload: preload.init(),
    page: NoPage,
  )
}

// State update helpers
pub fn set_route(model: Model, route: Route) -> Model {
  Model(..model, route: route)
}

pub fn set_user(model: Model, user: User) -> Model {
  Model(..model, user: Some(user))
}

pub fn reset_for_logout(model: Model) -> Model {
  let fresh = init()
  Model(
    ..fresh,
    project_name: model.project_name,
    project_description: model.project_description,
    checking_session: False,
    page: NoPage,
  )
}

pub fn set_loading(model: Model, loading: Bool) -> Model {
  Model(..model, loading: loading)
}

pub fn set_error(model: Model, error: Option(String)) -> Model {
  Model(..model, error: error)
}

pub fn set_success(model: Model, message: String) -> Model {
  Model(..model, success_message: Some(message))
}

// Cache helpers
pub fn cache_study(model: Model, study: Study) -> Model {
  let studies = dict.insert(model.studies, study.study_uid, study)
  Model(..model, studies: studies)
}

pub fn cache_series(model: Model, s: Series) -> Model {
  let series = dict.insert(model.series, s.series_uid, s)
  Model(..model, series: series)
}

pub fn cache_record(model: Model, record: Record) -> Model {
  case record.id {
    Some(id) -> {
      let records = dict.insert(model.records, int.to_string(id), record)
      Model(..model, records: records)
    }
    None -> model
  }
}

pub fn cache_record_type(model: Model, record_type: RecordType) -> Model {
  let record_types = dict.insert(model.record_types, record_type.name, record_type)
  Model(..model, record_types: record_types)
}

pub fn cache_patient(model: Model, patient: Patient) -> Model {
  let patients = dict.insert(model.patients, patient.id, patient)
  Model(..model, patients: patients)
}
