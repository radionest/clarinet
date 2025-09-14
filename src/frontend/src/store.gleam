// Global state management
import gleam/option.{type Option, None, Some}
import gleam/dict.{type Dict}
import gleam/json.{type Json}
import gleam/result
import router.{type Route}
import api/types.{type ApiConfig, type ApiError}
import api/models.{
  type User, type Study, type Task, type TaskDesign, type Patient,
  type StudyForm, type TaskDesignForm, type PatientForm
}

// Application state model
pub type Model {
  Model(
    // Navigation
    route: Route,

    // Authentication
    user: Option(User),
    token: Option(String),
    api_config: ApiConfig,

    // UI State
    loading: Bool,
    error: Option(String),
    success_message: Option(String),

    // Data caches
    studies: Dict(String, Study),
    tasks: Dict(String, Task),
    task_designs: Dict(String, TaskDesign),
    patients: Dict(String, Patient),
    users: Dict(String, User),

    // Form states
    study_form: Option(StudyForm),
    task_design_form: Option(TaskDesignForm),
    patient_form: Option(PatientForm),
    form_errors: Dict(String, String),

    // List views
    studies_list: List(Study),
    tasks_list: List(Task),
    users_list: List(User),

    // Pagination
    current_page: Int,
    items_per_page: Int,
    total_items: Int,

    // Filters
    search_query: String,
    active_filters: Dict(String, String),

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
  LoginSubmit(username: String, password: String)
  LoginSuccess(token: String, user: User)
  LoginError(ApiError)
  Logout
  LogoutComplete

  // Data loading
  LoadStudies
  StudiesLoaded(Result(List(Study), ApiError))
  LoadStudyDetail(id: String)
  StudyDetailLoaded(Result(Study, ApiError))

  LoadTasks
  TasksLoaded(Result(List(Task), ApiError))
  LoadTaskDetail(id: String)
  TaskDetailLoaded(Result(Task, ApiError))

  LoadUsers
  UsersLoaded(Result(List(User), ApiError))

  // Form handling
  UpdateStudyForm(StudyForm)
  SubmitStudyForm
  StudyFormSubmitted(Result(Study, ApiError))

  UpdateTaskDesignForm(TaskDesignForm)
  UpdateTaskDesignSchema(Json)
  SubmitTaskDesignForm
  TaskDesignFormSubmitted(Result(TaskDesign, ApiError))

  UpdatePatientForm(PatientForm)
  SubmitPatientForm
  PatientFormSubmitted(Result(Patient, ApiError))

  // Task execution
  SubmitTaskResult(task_id: String, result: Json)
  TaskResultSaved(Result(Task, ApiError))

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
    token: None,
    api_config: api.client.create_client(get_base_url()),
    loading: False,
    error: None,
    success_message: None,
    studies: dict.new(),
    tasks: dict.new(),
    task_designs: dict.new(),
    patients: dict.new(),
    users: dict.new(),
    study_form: None,
    task_design_form: None,
    patient_form: None,
    form_errors: dict.new(),
    studies_list: [],
    tasks_list: [],
    users_list: [],
    current_page: 1,
    items_per_page: 20,
    total_items: 0,
    search_query: "",
    active_filters: dict.new(),
    modal_open: False,
    modal_content: NoModal,
  )
}

// Get base URL from environment
fn get_base_url() -> String {
  // In production, this would come from environment config
  case is_development() {
    True -> "http://localhost:8000"
    False -> ""  // Use same origin in production
  }
}

// Check if in development mode
@external(javascript, "./ffi/utils.js", "isDevelopment")
fn is_development() -> Bool

// State update helpers
pub fn set_route(model: Model, route: Route) -> Model {
  Model(..model, route: route)
}

pub fn set_auth(model: Model, token: String, user: User) -> Model {
  Model(
    ..model,
    token: Some(token),
    user: Some(user),
    api_config: api.client.with_token(model.api_config, token),
  )
}

pub fn clear_auth(model: Model) -> Model {
  Model(
    ..model,
    token: None,
    user: None,
    api_config: api.client.create_client(get_base_url()),
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
  case study.id {
    Some(id) -> {
      let studies = dict.insert(model.studies, int.to_string(id), study)
      Model(..model, studies: studies)
    }
    None -> model
  }
}

pub fn cache_task(model: Model, task: Task) -> Model {
  case task.id {
    Some(id) -> {
      let tasks = dict.insert(model.tasks, int.to_string(id), task)
      Model(..model, tasks: tasks)
    }
    None -> model
  }
}

pub fn cache_task_design(model: Model, design: TaskDesign) -> Model {
  case design.id {
    Some(id) -> {
      let designs = dict.insert(model.task_designs, int.to_string(id), design)
      Model(..model, task_designs: designs)
    }
    None -> model
  }
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