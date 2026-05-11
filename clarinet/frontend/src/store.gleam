// Global state management
import api/info.{type ProjectInfo, type ViewerInfo}
import api/models.{type Record, type User}
import api/types.{type ApiError}
import cache
import clarinet_frontend/i18n
import gleam/option.{type Option, None, Some}
import pages/admin as admin_page
import pages/admin/reports as admin_reports_page
import pages/admin/workflow as admin_workflow_page
import pages/home as home_page
import pages/login as login_page
import pages/patients/detail as patient_detail_page
import pages/patients/list as patients_list_page
import pages/patients/new as patient_new_page
import pages/record_types/detail as record_type_detail_page
import pages/record_types/edit as record_type_edit_page
import pages/record_types/list as record_types_list_page
import pages/records/execute as record_execute_page
import pages/records/list as records_list_page
import pages/records/new as record_new_page
import pages/register as register_page
import pages/series/detail as series_detail_page
import pages/studies/detail as study_detail_page
import pages/studies/list as studies_list_page
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
    // Global entity caches
    cache: cache.Model,
    // Modal state
    modal_open: Bool,
    modal_content: ModalContent,
    fail_reason: String,
    // Preload
    preload: preload.Model,
    // Viewers
    viewers: List(ViewerInfo),
    // Locale
    locale: i18n.Locale,
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
  AdminReportsPage(admin_reports_page.Model)
  AdminWorkflowPage(admin_workflow_page.Model)
  HomePage(home_page.Model)
}

// Modal content types
pub type ModalContent {
  NoModal
  ConfirmDelete(resource: String, id: String)
  FailRecordPrompt(record_id: String)
  /// Modal hosting the create-record form. Stores the embedded page model
  /// so the modal MVU is delegated through `RecordNewModalMsg`.
  CreateRecord(record_new_page.Model)
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
  /// Same page module as RecordNewMsg, but delegated to the embedded
  /// instance living inside `ModalContent.CreateRecord`.
  RecordNewModalMsg(record_new_page.Msg)

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

  // Admin page delegation
  AdminMsg(admin_page.Msg)
  AdminReportsMsg(admin_reports_page.Msg)
  AdminWorkflowMsg(admin_workflow_page.Msg)

  // UI Actions
  SetError(Option(String))
  ClearError
  ClearSuccessMessage
  /// No-op message — used as the click handler on a modal's content
  /// surface so clicks don't bubble to the backdrop close handler.
  NoOp

  OpenModal(ModalContent)
  CloseModal
  ConfirmModalAction

  // Project info
  ProjectInfoLoaded(Result(ProjectInfo, ApiError))

  // Cache delegation (all data loading lives in cache.gleam)
  CacheMsg(cache.Msg)

  // Manual fail record (modal-based)
  UpdateFailReason(String)
  ConfirmFailRecord(record_id: String)
  FailRecordResult(Result(Record, ApiError))

  // Preload delegation
  PreloadMsg(preload.Msg)

  // Locale
  SetLocale(i18n.Locale)
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
    cache: cache.init(),
    modal_open: False,
    modal_content: NoModal,
    fail_reason: "",
    preload: preload.init(),
    viewers: [],
    locale: i18n.En,
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
    viewers: model.viewers,
    locale: model.locale,
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
