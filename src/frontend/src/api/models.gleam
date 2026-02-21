// Static type definitions for core models matching backend SQLModel
import api/types.{
  type DicomQueryLevel, type RecordStatus, type UserRole as UserRoleEnum,
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
    records: Option(List(Record)),
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
    records: Option(List(Record)),
  )
}

// Record Type model (matching backend)
pub type RecordType {
  RecordType(
    name: String,
    // Primary key
    description: Option(String),
    label: Option(String),
    slicer_script: Option(String),
    slicer_script_args: Option(Dict(String, String)),
    // SlicerArgs
    slicer_result_validator: Option(String),
    slicer_result_validator_args: Option(Dict(String, String)),
    data_schema: Option(Dict(String, Json)),
    // DataSchema for dynamic form
    role_name: Option(String),
    max_users: Option(Int),
    min_users: Option(Int),
    level: DicomQueryLevel,
    constraint_role: Option(Role),
    records: Option(List(Record)),
  )
}

// Record model (matching backend)
pub type Record {
  Record(
    id: Option(Int),
    // Primary key
    context_info: Option(String),
    status: RecordStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    record_type_name: String,
    user_id: Option(String),
    patient_id: String,
    study_anon_uid: Option(String),
    series_anon_uid: Option(String),
    clarinet_storage_path: Option(String),
    patient: Option(Patient),
    study: Option(Study),
    series: Option(Series),
    record_type: Option(RecordType),
    user: Option(User),
    data: Option(Dict(String, Json)),
    // RecordData
    created_at: Option(String),
    changed_at: Option(String),
    started_at: Option(String),
    finished_at: Option(String),
    // Computed fields
    radiant: Option(String),
    working_folder: Option(String),
    slicer_args_formatted: Option(Dict(String, String)),
    slicer_validator_args_formatted: Option(Dict(String, String)),
    slicer_all_args_formatted: Option(Dict(String, String)),
  )
}

// User model (matching backend fastapi-users SQLModelBaseUserDB)
pub type User {
  User(
    id: String,
    // UUID Primary key
    email: String,
    // Unique email used for identification
    hashed_password: Option(String),
    // Won't be sent from API usually
    is_active: Bool,
    is_superuser: Bool,
    is_verified: Bool,
    roles: Option(List(Role)),
    records: Option(List(Record)),
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
    records: Option(List(Record)),
    // Computed field
    working_folder: Option(String),
  )
}

// Authentication models
pub type LoginRequest {
  LoginRequest(email: String, password: String)
}

// Login response - returns user data only (cookie auth handled automatically)
pub type LoginResponse {
  LoginResponse(user: User)
}

pub type RegisterRequest {
  RegisterRequest(
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
    records: List(Record),
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

pub type RecordTypeCreate {
  RecordTypeCreate(
    name: String,
    description: Option(String),
    label: Option(String),
    slicer_script: Option(String),
    slicer_script_args: Option(Dict(String, String)),
    slicer_result_validator: Option(String),
    slicer_result_validator_args: Option(Dict(String, String)),
    data_schema: Option(Dict(String, Json)),
    role_name: Option(String),
    max_users: Option(Int),
    min_users: Option(Int),
    level: DicomQueryLevel,
  )
}

pub type RecordCreate {
  RecordCreate(
    context_info: Option(String),
    status: RecordStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    record_type_name: String,
    user_id: Option(String),
    patient_id: String,
  )
}

pub type RecordRead {
  RecordRead(
    id: Int,
    context_info: Option(String),
    status: RecordStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    record_type_name: String,
    user_id: Option(String),
    patient_id: String,
    data: Option(Dict(String, Json)),
    patient: Patient,
    study: Study,
    series: Option(Series),
    record_type: RecordType,
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
    records: List(RecordRead),
    working_folder: Option(String),
  )
}

pub type UserCreate {
  UserCreate(
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
    allowed_record_types: Option(List(RecordType)),
    users: Option(List(User)),
  )
}

// Admin dashboard statistics
pub type AdminStats {
  AdminStats(
    total_studies: Int,
    total_records: Int,
    total_users: Int,
    total_patients: Int,
    records_by_status: Dict(String, Int),
  )
}
