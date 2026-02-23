// Static type definitions for core models matching backend SQLModel
import api/types.{type DicomQueryLevel, type RecordStatus}
import gleam/dict.{type Dict}
import gleam/option.{type Option}

// Patient model (matching backend)
pub type Patient {
  Patient(
    id: String,
    // Primary key in backend
    name: Option(String),
    anon_id: Option(String),
    anon_name: Option(String),
    auto_id: Option(Int),
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

// File definition for RecordType input/output files (matching backend)
pub type FileDefinition {
  FileDefinition(
    name: String,
    pattern: String,
    description: Option(String),
    required: Bool,
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
    data_schema: Option(String),
    // JSON Schema string for dynamic form (formosh)
    role_name: Option(String),
    max_users: Option(Int),
    min_users: Option(Int),
    level: DicomQueryLevel,
    input_files: Option(List(FileDefinition)),
    output_files: Option(List(FileDefinition)),
    constraint_role: Option(String),
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
    files: Option(Dict(String, String)),
    patient: Option(Patient),
    study: Option(Study),
    series: Option(Series),
    record_type: Option(RecordType),
    user: Option(User),
    data: Option(String),
    // RecordData as JSON string
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

// User model (matching backend fastapi-users UserRead schema)
pub type User {
  User(
    id: String,
    // UUID Primary key
    email: String,
    // Unique email used for identification
    is_active: Bool,
    is_superuser: Bool,
    is_verified: Bool,
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
  RegisterRequest(email: String, password: String)
}

// Form data types for creating/updating models
pub type PatientCreate {
  PatientCreate(
    id: String,
    name: Option(String),
    anon_id: Option(String),
    anon_name: Option(String),
  )
}

pub type PatientRead {
  PatientRead(
    id: String,
    name: Option(String),
    anon_id: Option(String),
    anon_name: Option(String),
    auto_id: Option(Int),
    studies: List(Study),
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
    data_schema: Option(String),
    role_name: Option(String),
    max_users: Option(Int),
    min_users: Option(Int),
    level: DicomQueryLevel,
    input_files: Option(List(FileDefinition)),
    output_files: Option(List(FileDefinition)),
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
    data: Option(String),
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

// Per-status record counts for a record type
pub type RecordTypeStatusCounts {
  RecordTypeStatusCounts(
    pending: Int,
    inwork: Int,
    finished: Int,
    failed: Int,
    pause: Int,
  )
}

// Record type with aggregate statistics
pub type RecordTypeStats {
  RecordTypeStats(
    name: String,
    description: Option(String),
    label: Option(String),
    level: String,
    role_name: Option(String),
    min_users: Option(Int),
    max_users: Option(Int),
    total_records: Int,
    records_by_status: RecordTypeStatusCounts,
    unique_users: Int,
  )
}
