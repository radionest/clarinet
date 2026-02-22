// Global state management
import api/models.{
  type AdminStats, type Patient, type Study, type Record, type RecordType,
  type User,
}
import api/types.{type ApiError}
import gleam/dict.{type Dict}
import gleam/dynamic
import gleam/int
import gleam/json.{type Json}
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/result
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
    patient_form: Option(dynamic.Dynamic),
    form_errors: Dict(String, String),
    // List views
    studies_list: List(Study),
    records_list: List(Record),
    users_list: List(User),
    // Pagination
    current_page: Int,
    items_per_page: Int,
    total_items: Int,
    // Filters
    search_query: String,
    active_filters: Dict(String, String),
    // Admin
    admin_stats: Option(AdminStats),
    admin_editing_record_id: Option(Int),
    // Modal state
    modal_open: Bool,
    modal_content: ModalContent,
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

  LoadRecords
  RecordsLoaded(Result(List(Record), ApiError))
  LoadRecordDetail(id: String)
  RecordDetailLoaded(Result(Record, ApiError))

  LoadUsers
  UsersLoaded(Result(List(User), ApiError))

  LoadAdminStats
  AdminStatsLoaded(Result(AdminStats, ApiError))

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

  UpdatePatientForm(dynamic.Dynamic)
  SubmitPatientForm
  PatientFormSubmitted(Result(Patient, ApiError))

  // Record data submission
  SubmitRecordData(record_id: String, data: Json)
  RecordDataSaved(Result(Record, ApiError))

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
    records: dict.new(),
    record_types: dict.new(),
    patients: dict.new(),
    users: dict.new(),
    study_form: None,
    record_type_form: None,
    patient_form: None,
    form_errors: dict.new(),
    studies_list: [],
    records_list: [],
    users_list: [],
    current_page: 1,
    items_per_page: 20,
    total_items: 0,
    search_query: "",
    active_filters: dict.new(),
    admin_stats: None,
    admin_editing_record_id: None,
    modal_open: False,
    modal_content: NoModal,
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

// Update a record in the records list by replacing the matching entry
pub fn update_record_in_list(model: Model, updated: Record) -> Model {
  let new_list =
    list.map(model.records_list, fn(r) {
      case r.id == updated.id {
        True -> updated
        False -> r
      }
    })
  // Also update the dict cache
  let new_dict = case updated.id {
    Some(id) -> dict.insert(model.records, int.to_string(id), updated)
    None -> model.records
  }
  Model(..model, records_list: new_list, records: new_dict)
}
