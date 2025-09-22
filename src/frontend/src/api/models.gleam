// Static type definitions for core models matching backend SQLModel
import api/types.{
  type DicomQueryLevel, type TaskStatus, type UserRole as UserRoleEnum,
}
import gleam/dict.{type Dict}
import gleam/json.{type Json}
import gleam/option.{type Option}

// Patient model (matching backend)
pub type Patient {
  Patient(
    id: String,
    // Primary key in backend
    anon_id: Option(String),
    anon_name: Option(String),
    created_at: Option(String),
    updated_at: Option(String),
    studies: Option(List(Study)),
    tasks: Option(List(Task)),
  )
}

// Study model (matching backend)
pub type Study {
  Study(
    study_uid: String,
    // Primary key (DicomUID)
    date: String,
    // date type in backend
    anon_uid: Option(String),
    patient_id: String,
    patient: Option(Patient),
    series: Option(List(Series)),
    tasks: Option(List(Task)),
  )
}

// Task Design model (matching backend)
pub type TaskDesign {
  TaskDesign(
    name: String,
    // Primary key
    description: Option(String),
    label: Option(String),
    slicer_script: Option(String),
    slicer_script_args: Option(Dict(String, String)),
    // SlicerArgs
    slicer_result_validator: Option(String),
    slicer_result_validator_args: Option(Dict(String, String)),
    result_schema: Option(Dict(String, Json)),
    // ResultSchema for dynamic form
    role_name: Option(String),
    max_users: Option(Int),
    min_users: Option(Int),
    level: DicomQueryLevel,
    constraint_role: Option(Role),
    tasks: Option(List(Task)),
  )
}

// Task model (matching backend)
pub type Task {
  Task(
    id: Option(Int),
    // Primary key
    info: Option(String),
    status: TaskStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    task_design_id: String,
    user_id: Option(String),
    patient_id: String,
    study_anon_uid: Option(String),
    series_anon_uid: Option(String),
    clarinet_storage_path: Option(String),
    patient: Option(Patient),
    study: Option(Study),
    series: Option(Series),
    task_design: Option(TaskDesign),
    user: Option(User),
    result: Option(Dict(String, Json)),
    // TaskResult
    created_at: Option(String),
    changed_at: Option(String),
    started_at: Option(String),
    finished_at: Option(String),
    // Computed fields
    radiant: Option(String),
    working_folder: Option(String),
    slicer_args_formated: Option(Dict(String, String)),
    slicer_validator_args_formated: Option(Dict(String, String)),
    slicer_all_args_formated: Option(Dict(String, String)),
  )
}

// User model (matching backend)
pub type User {
  User(
    id: String,
    // Primary key
    username: String,
    email: String,
    hashed_password: Option(String),
    // Won't be sent from API usually
    is_active: Bool,
    is_superuser: Bool,
    is_verified: Bool,
    roles: Option(List(Role)),
    tasks: Option(List(Task)),
  )
}

// Series model (matching backend)
pub type Series {
  Series(
    series_uid: String,
    // Primary key (DicomUID)
    series_description: Option(String),
    series_number: Int,
    anon_uid: Option(String),
    study_uid: String,
    study: Option(Study),
    tasks: Option(List(Task)),
    // Computed field
    working_folder: Option(String),
  )
}

// Authentication models
pub type LoginRequest {
  LoginRequest(username: String, password: String)
}

// Login response - returns user data only (cookie auth handled automatically)
pub type LoginResponse {
  LoginResponse(user: User)
}

pub type RegisterRequest {
  RegisterRequest(
    username: String,
    email: String,
    password: String,
    full_name: Option(String),
  )
}

// Form data types for creating/updating models
pub type PatientCreate {
  PatientCreate(id: String, anon_id: Option(String), anon_name: Option(String))
}

pub type PatientRead {
  PatientRead(
    id: String,
    anon_id: Option(String),
    anon_name: Option(String),
    studies: List(Study),
    tasks: List(Task),
  )
}

pub type StudyCreate {
  StudyCreate(
    study_uid: String,
    date: String,
    patient_id: String,
    anon_uid: Option(String),
  )
}

pub type StudyRead {
  StudyRead(
    study_uid: String,
    date: String,
    anon_uid: Option(String),
    patient_id: String,
    patient: Patient,
    series: List(Series),
  )
}

pub type TaskDesignCreate {
  TaskDesignCreate(
    name: String,
    description: Option(String),
    label: Option(String),
    slicer_script: Option(String),
    slicer_script_args: Option(Dict(String, String)),
    slicer_result_validator: Option(String),
    slicer_result_validator_args: Option(Dict(String, String)),
    result_schema: Option(Dict(String, Json)),
    role_name: Option(String),
    max_users: Option(Int),
    min_users: Option(Int),
    level: DicomQueryLevel,
  )
}

pub type TaskCreate {
  TaskCreate(
    info: Option(String),
    status: TaskStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    task_design_id: String,
    user_id: Option(String),
    patient_id: String,
  )
}

pub type TaskRead {
  TaskRead(
    id: Int,
    info: Option(String),
    status: TaskStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    task_design_id: String,
    user_id: Option(String),
    patient_id: String,
    result: Option(Dict(String, Json)),
    patient: Patient,
    study: Study,
    series: Option(Series),
    task_design: TaskDesign,
  )
}

pub type SeriesCreate {
  SeriesCreate(
    series_uid: String,
    series_description: Option(String),
    series_number: Option(Int),
    anon_uid: Option(String),
    study_uid: String,
  )
}

pub type SeriesRead {
  SeriesRead(
    series_uid: String,
    series_description: Option(String),
    series_number: Int,
    anon_uid: Option(String),
    study_uid: String,
    study: Study,
    tasks: List(TaskRead),
    working_folder: Option(String),
  )
}

pub type UserCreate {
  UserCreate(
    username: String,
    email: String,
    password: String,
    is_active: Option(Bool),
    is_superuser: Option(Bool),
    is_verified: Option(Bool),
  )
}

pub type UserRead {
  UserRead(
    id: String,
    username: String,
    email: String,
    is_active: Bool,
    is_superuser: Bool,
    is_verified: Bool,
    roles: List(Role),
  )
}

pub type Role {
  Role(
    name: String,
    description: Option(String),
    allowed_task_designs: Option(List(TaskDesign)),
    users: Option(List(User)),
  )
}
