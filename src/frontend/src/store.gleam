// Global state management
import api/models.{
  type AdminStats, type PacsStudyWithSeries, type Patient, type RecordTypeStats,
  type Series, type Study, type Record, type RecordType, type User,
}
import api/types.{type ApiError}
import gleam/dict.{type Dict}
import gleam/dynamic
import gleam/int
import gleam/json.{type Json}
import gleam/option.{type Option, None, Some}
import router.{type Route}

// Application state model
pub type Model {
  Model(
    // Navigation
    route: Route,
    // Authentication (using cookie-based auth)
    user: Option(User),
    checking_session: Bool,
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
    // Auth form states (controlled inputs)
    login_email: String,
    login_password: String,
    register_email: String,
    register_password: String,
    register_password_confirm: String,
    // Form states
    study_form: Option(dynamic.Dynamic),
    // Will hold form data dynamically
    record_type_form: Option(dynamic.Dynamic),
    patient_form_id: String,
    patient_form_name: String,
    form_errors: Dict(String, String),
    // Pagination
    current_page: Int,
    items_per_page: Int,
    total_items: Int,
    // Filters
    search_query: String,
    active_filters: Dict(String, String),
    // Admin
    admin_stats: Option(AdminStats),
    record_type_stats: Option(List(RecordTypeStats)),
    admin_editing_record_id: Option(Int),
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
  )
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
  LoginSubmit(email: String, password: String)
  LoginSuccess(user: User)
  LoginError(ApiError)
  LoginUpdateEmail(String)
  LoginUpdatePassword(String)
  RegisterSubmit(email: String, password: String)
  RegisterSuccess(user: User)
  RegisterError(ApiError)
  RegisterUpdateEmail(String)
  RegisterUpdatePassword(String)
  RegisterUpdatePasswordConfirm(String)
  Logout
  LogoutComplete

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

  LoadAdminStats
  AdminStatsLoaded(Result(AdminStats, ApiError))

  LoadRecordTypeStats
  RecordTypeStatsLoaded(Result(List(RecordTypeStats), ApiError))

  // Admin record assignment
  AdminToggleAssignDropdown(record_id: Option(Int))
  AdminAssignUser(record_id: Int, user_id: String)
  AdminAssignUserResult(Result(Record, ApiError))

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

  // Record data submission
  SubmitRecordData(record_id: String, data: Json)
  RecordDataSaved(Result(Record, ApiError))

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
    loading: False,
    error: None,
    success_message: None,
    login_email: "",
    login_password: "",
    register_email: "",
    register_password: "",
    register_password_confirm: "",
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
    form_errors: dict.new(),
    current_page: 1,
    items_per_page: 20,
    total_items: 0,
    search_query: "",
    active_filters: dict.new(),
    admin_stats: None,
    record_type_stats: None,
    admin_editing_record_id: None,
    modal_open: False,
    modal_content: NoModal,
    pacs_studies: [],
    pacs_loading: False,
    pacs_importing: None,
    slicer_loading: False,
    slicer_available: None,
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

pub fn clear_auth_forms(model: Model) -> Model {
  Model(
    ..model,
    login_email: "",
    login_password: "",
    register_email: "",
    register_password: "",
    register_password_confirm: "",
  )
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

// Update a record in the records dict cache
pub fn update_record(model: Model, updated: Record) -> Model {
  case updated.id {
    Some(id) -> {
      let records = dict.insert(model.records, int.to_string(id), updated)
      Model(..model, records: records)
    }
    None -> model
  }
}
