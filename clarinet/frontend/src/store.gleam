// Global state management
import api/info.{type ProjectInfo}
import api/models.{
  type PacsStudyWithSeries, type Patient, type RecordTypeStats,
  type Series, type Study, type Record, type RecordType, type User,
}
import pages/admin as admin_page
import pages/login as login_page
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
    // Will hold form data dynamically
    record_type_form: Option(dynamic.Dynamic),
    patient_form_id: String,
    patient_form_name: String,
    // Record creation form
    record_form_record_type_name: String,
    record_form_patient_id: String,
    record_form_study_uid: String,
    record_form_series_uid: String,
    record_form_user_id: String,
    record_form_parent_record_id: String,
    record_form_context_info: String,
    record_form_studies: List(Study),
    record_form_series: List(Series),
    form_errors: Dict(String, String),
    // Pagination
    current_page: Int,
    items_per_page: Int,
    total_items: Int,
    // Filters
    search_query: String,
    active_filters: Dict(String, String),
    // Admin
    record_type_stats: Option(List(RecordTypeStats)),
    // Modal state
    modal_open: Bool,
    modal_content: ModalContent,
    // PACS state
    pacs_studies: List(PacsStudyWithSeries),
    pacs_loading: Bool,
    pacs_importing: Option(String),
    // Slicer state
    slicer_loading: Bool,
    slicer_available: Option(Bool),
    slicer_ping_timer: Option(global.TimerID),
    // Hydrated schemas cache (record_id -> schema JSON string)
    hydrated_schemas: Dict(String, String),
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
}

// Modal content types
pub type ModalContent {
  NoModal
  ConfirmDelete(resource: String, id: String)
  ViewDetails(resource: String, data: Json)
  EditForm(resource: String, id: Option(String))
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
  AnonymizePatient(id: String)
  PatientAnonymized(Result(Patient, ApiError))
  DeletePatient(id: String)
  PatientDeleted(Result(Nil, ApiError))
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

  UpdatePatientFormId(String)
  UpdatePatientFormName(String)
  SubmitPatientForm
  PatientFormSubmitted(Result(Patient, ApiError))

  // Record creation form
  UpdateRecordFormRecordType(String)
  UpdateRecordFormPatient(String)
  UpdateRecordFormStudy(String)
  UpdateRecordFormSeries(String)
  UpdateRecordFormUser(String)
  UpdateRecordFormParentRecordId(String)
  UpdateRecordFormContextInfo(String)
  RecordFormStudiesLoaded(Result(List(Study), ApiError))
  RecordFormSeriesLoaded(Result(List(Series), ApiError))
  LoadRecordTypes
  RecordTypesLoaded(Result(List(RecordType), ApiError))
  SubmitRecordForm
  RecordFormSubmitted(Result(Record, ApiError))

  // Slicer record completion (no form)
  CompleteRecord(record_id: String)
  CompleteRecordResult(record_id: String, result: Result(Record, ApiError))

  // Re-submit finished record (no form, PATCH)
  ResubmitRecord(record_id: String)
  ResubmitRecordResult(record_id: String, result: Result(Record, ApiError))

  // Formosh form events
  FormSubmitSuccess(record_id: String)
  FormSubmitError(error: String)

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

  // Search and filters
  UpdateSearchQuery(String)
  AddFilter(key: String, value: String)
  RemoveFilter(key: String)
  ClearFilters

  // Pagination
  SetPage(Int)
  SetItemsPerPage(Int)

  // PACS operations
  SearchPacsStudies(patient_id: String)
  PacsStudiesLoaded(Result(List(PacsStudyWithSeries), ApiError))
  ImportPacsStudy(study_uid: String, patient_id: String)
  PacsStudyImported(Result(Study, ApiError))
  ClearPacsResults

  // Slicer operations
  OpenInSlicer(record_id: String)
  SlicerOpenResult(Result(dynamic.Dynamic, ApiError))
  SlicerValidate(record_id: String)
  SlicerValidateResult(Result(dynamic.Dynamic, ApiError))
  SlicerClearScene
  SlicerClearSceneResult(Result(dynamic.Dynamic, ApiError))
  SlicerPing
  SlicerPingResult(Result(dynamic.Dynamic, ApiError))
  SlicerPingTimerStarted(global.TimerID)
  StopSlicerPingTimer

  // Schema hydration
  LoadHydratedSchema(record_id: String)
  HydratedSchemaLoaded(record_id: String, result: Result(String, ApiError))

  // Project info
  ProjectInfoLoaded(Result(ProjectInfo, ApiError))

  // Auto-assign
  AutoAssignResult(Result(Record, ApiError))

  // Restart auto task
  RestartRecord(record_id: String)
  RestartRecordResult(Result(Record, ApiError))

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
    patient_form_id: "",
    patient_form_name: "",
    record_form_record_type_name: "",
    record_form_patient_id: "",
    record_form_study_uid: "",
    record_form_series_uid: "",
    record_form_user_id: "",
    record_form_parent_record_id: "",
    record_form_context_info: "",
    record_form_studies: [],
    record_form_series: [],
    form_errors: dict.new(),
    current_page: 1,
    items_per_page: 20,
    total_items: 0,
    search_query: "",
    active_filters: dict.new(),
    record_type_stats: None,
    modal_open: False,
    modal_content: NoModal,
    pacs_studies: [],
    pacs_loading: False,
    pacs_importing: None,
    slicer_loading: False,
    slicer_available: None,
    slicer_ping_timer: None,
    hydrated_schemas: dict.new(),
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
  // Use study_uid as the key
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
  // Use name as the key for RecordType
  let record_types = dict.insert(model.record_types, record_type.name, record_type)
  Model(..model, record_types: record_types)
}

pub fn cache_patient(model: Model, patient: Patient) -> Model {
  let patients = dict.insert(model.patients, patient.id, patient)
  Model(..model, patients: patients)
}

pub fn clear_patient_form(model: Model) -> Model {
  Model(..model, patient_form_id: "", patient_form_name: "")
}

pub fn clear_record_form(model: Model) -> Model {
  Model(
    ..model,
    record_form_record_type_name: "",
    record_form_patient_id: "",
    record_form_study_uid: "",
    record_form_series_uid: "",
    record_form_user_id: "",
    record_form_parent_record_id: "",
    record_form_context_info: "",
    record_form_studies: [],
    record_form_series: [],
  )
}

// Form helpers
pub fn set_form_error(model: Model, field: String, error: String) -> Model {
  let errors = dict.insert(model.form_errors, field, error)
  Model(..model, form_errors: errors)
}

pub fn clear_form_errors(model: Model) -> Model {
  Model(..model, form_errors: dict.new())
}

// Filter helpers
pub fn apply_filter(model: Model, key: String, value: String) -> Model {
  let filters = dict.insert(model.active_filters, key, value)
  Model(..model, active_filters: filters)
}

pub fn remove_filter(model: Model, key: String) -> Model {
  let filters = dict.delete(model.active_filters, key)
  Model(..model, active_filters: filters)
}

pub fn clear_filters(model: Model) -> Model {
  Model(..model, active_filters: dict.new(), search_query: "")
}

// PACS helpers
pub fn set_pacs_loading(model: Model, loading: Bool) -> Model {
  Model(..model, pacs_loading: loading)
}

pub fn set_pacs_studies(model: Model, studies: List(PacsStudyWithSeries)) -> Model {
  Model(..model, pacs_studies: studies, pacs_loading: False)
}

pub fn clear_pacs(model: Model) -> Model {
  Model(..model, pacs_studies: [], pacs_loading: False, pacs_importing: None)
}
