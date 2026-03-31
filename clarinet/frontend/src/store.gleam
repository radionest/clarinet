// Global state management
import api/info.{type ProjectInfo}
import api/models.{
  type Patient, type RecordTypeStats, type RoleMatrix,
  type Series, type Study, type Record, type RecordType, type User,
}
import pages/admin as admin_page
import pages/login as login_page
import pages/patients/detail as patient_detail_page
import pages/patients/list as patients_list_page
import pages/patients/new as patient_new_page
import pages/records/execute as record_execute_page
import pages/records/list as records_list_page
import pages/records/new as record_new_page
import pages/register as register_page
import api/types.{type ApiError}
import gleam/dict.{type Dict}
import gleam/dynamic
import gleam/int
import gleam/json.{type Json}
import gleam/option.{type Option, None, Some}
import plinth/javascript/global
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
    // Form states
    study_form: Option(dynamic.Dynamic),
    record_type_form: Option(dynamic.Dynamic),
    // Pagination
    current_page: Int,
    items_per_page: Int,
    total_items: Int,
    // Search
    search_query: String,
    // Admin
    record_type_stats: Option(List(RecordTypeStats)),
    // Modal state
    modal_open: Bool,
    modal_content: ModalContent,
    // Role matrix
    role_matrix: Option(RoleMatrix),
    role_toggling: Option(#(String, String)),
    // Preload state
    preload_timer: Option(global.TimerID),
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
}

// Modal content types
pub type ModalContent {
  NoModal
  ConfirmDelete(resource: String, id: String)
  ViewDetails(resource: String, data: Json)
  EditForm(resource: String, id: Option(String))
  PreloadProgress(
    viewer_url: String,
    task_id: String,
    study_uid: String,
    received: Int,
    total: Option(Int),
    status: String,
  )
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

  // Data loading
  LoadStudies
  StudiesLoaded(Result(List(Study), ApiError))
  LoadStudyDetail(id: String)
  StudyDetailLoaded(Result(Study, ApiError))

  LoadSeriesDetail(id: String)
  SeriesDetailLoaded(Result(Series, ApiError))

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
  DeleteStudy(study_uid: String)
  StudyDeleted(Result(Nil, ApiError))

  LoadRecordTypeStats
  RecordTypeStatsLoaded(Result(List(RecordTypeStats), ApiError))

  // Admin page delegation
  AdminMsg(admin_page.Msg)

  // Form handling
  UpdateStudyForm(dynamic.Dynamic)
  SubmitStudyForm
  StudyFormSubmitted(Result(Study, ApiError))

  UpdateRecordTypeForm(dynamic.Dynamic)
  UpdateRecordTypeSchema(Json)
  SubmitRecordTypeForm
  RecordTypeFormSubmitted(Result(RecordType, ApiError))

  // Record types loading (used by record_new page init + admin)
  LoadRecordTypes
  RecordTypesLoaded(Result(List(RecordType), ApiError))

  // RecordType edit
  LoadRecordTypeForEdit(name: String)
  RecordTypeForEditLoaded(Result(RecordType, ApiError))
  RecordTypeEditSuccess(name: String)
  RecordTypeEditError(error: String)

  // UI Actions
  SetLoading(Bool)
  SetError(Option(String))
  ClearError
  SetSuccessMessage(String)
  ClearSuccessMessage

  OpenModal(ModalContent)
  CloseModal
  ConfirmModalAction

  // Search
  UpdateSearchQuery(String)

  // Pagination
  SetPage(Int)
  SetItemsPerPage(Int)

  // Project info
  ProjectInfoLoaded(Result(ProjectInfo, ApiError))

  // Auto-assign
  AutoAssignResult(Result(Record, ApiError))

  // Restart auto task
  RestartRecord(record_id: String)
  RestartRecordResult(Result(Record, ApiError))

  // Role matrix
  LoadRoleMatrix
  RoleMatrixLoaded(Result(RoleMatrix, ApiError))
  ToggleUserRole(user_id: String, role_name: String, add: Bool)
  UserRoleToggled(Result(Nil, ApiError))

  // Preload
  StartPreload(viewer_url: String, study_uid: String)
  PreloadStarted(viewer_url: String, task_id: String, study_uid: String)
  PreloadPollTick(task_id: String, viewer_url: String, study_uid: String)
  PreloadProgressUpdate(
    task_id: String,
    viewer_url: String,
    study_uid: String,
    result: Result(dynamic.Dynamic, ApiError),
  )
  CancelPreload
  SetPreloadTimer(global.TimerID)

  // Misc
  NoOp
  RefreshData
  ShowSchemaError(String)
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
    study_form: None,
    record_type_form: None,
    current_page: 1,
    items_per_page: 20,
    total_items: 0,
    search_query: "",
    record_type_stats: None,
    modal_open: False,
    modal_content: NoModal,
    role_matrix: None,
    role_toggling: None,
    preload_timer: None,
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

pub fn clear_user(model: Model) -> Model {
  Model(..model, user: None)
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

pub fn clear_messages(model: Model) -> Model {
  Model(..model, error: None, success_message: None)
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
