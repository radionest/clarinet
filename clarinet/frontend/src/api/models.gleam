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
    study_description: Option(String),
    modalities_in_study: Option(String),
    patient_id: String,
    patient: Option(Patient),
    series: Option(List(Series)),
    records: Option(List(Record)),
  )
}

// File role enum (matching backend FileRole)
pub type FileRole {
  Input
  Output
  Intermediate
}

// File definition for RecordType file_registry (matching backend)
pub type FileDefinition {
  FileDefinition(
    name: String,
    pattern: String,
    description: Option(String),
    required: Bool,
    multiple: Bool,
    role: FileRole,
    level: Option(String),
  )
}

// Per-file link on a record (matching backend RecordFileLinkRead)
pub type RecordFileLink {
  RecordFileLink(name: String, filename: String, checksum: Option(String))
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
    ui_schema: Option(String),
    // formosh ui-schema string (presentation hints — widgets, ordering, placeholders)
    role_name: Option(String),
    max_records: Option(Int),
    min_records: Option(Int),
    unique_per_user: Bool,
    parent_required: Bool,
    inherit_user_from_parent: Bool,
    editable: Bool,
    edit_window_days: Option(Int),
    viewer_mode: String,
    // Per-RecordType viewer allowlist (matching ViewerInfo.name). None or empty
    // → all configured viewers; a non-empty list restricts the UI to those.
    allowed_viewers: Option(List(String)),
    level: DicomQueryLevel,
    file_registry: Option(List(FileDefinition)),
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
    // Markdown source; also rendered to safe HTML by the backend
    context_info_html: Option(String),
    status: RecordStatus,
    study_uid: Option(String),
    series_uid: Option(String),
    record_type_name: String,
    user_id: Option(String),
    patient_id: String,
    parent_record_id: Option(Int),
    study_anon_uid: Option(String),
    series_anon_uid: Option(String),
    viewer_study_uids: Option(List(String)),
    viewer_series_uids: Option(List(String)),
    clarinet_storage_path: Option(String),
    files: Option(Dict(String, String)),
    file_checksums: Option(Dict(String, String)),
    file_links: Option(List(RecordFileLink)),
    patient: Option(Patient),
    study: Option(Study),
    series: Option(Series),
    record_type: Option(RecordType),
    data: Option(String),
    // RecordData as JSON string
    created_at: Option(String),
    changed_at: Option(String),
    started_at: Option(String),
    finished_at: Option(String),
    // Computed fields
    radiant: Option(String),
    // Server-derived per-study anon ID: set only when
    // anon_per_study_patient_id is enabled and the study is anonymized;
    // display falls back to patient.anon_id otherwise
    display_anon_id: Option(String),
    // Server-side verdict: may the submitted data still be changed by
    // non-superusers (RecordType.editable + edit_window_days)
    is_editable: Bool,
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
    role_names: List(String),
  )
}

// Series model (matching backend)
pub type Series {
  Series(
    series_uid: String,
    // Primary key (DicomUID)
    series_description: Option(String),
    series_number: Int,
    modality: Option(String),
    instance_count: Option(Int),
    anon_uid: Option(String),
    study_uid: String,
    study: Option(Study),
    records: Option(List(Record)),
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

/// One active session row from GET /api/auth/sessions/active. `last_accessed`
/// is a backend ISO-8601 timestamp (UTC). `is_current` marks the session
/// making the request — the settings page renders it as "This device" with no
/// revoke button, so users can't sign themselves out by accident.
pub type SessionInfo {
  SessionInfo(
    token_preview: String,
    last_accessed: String,
    user_agent: Option(String),
    ip_address: Option(String),
    is_current: Bool,
  )
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
    max_records: Option(Int),
    min_records: Option(Int),
    unique_per_user: Bool,
    level: DicomQueryLevel,
    file_registry: Option(List(FileDefinition)),
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
    parent_record_id: Option(Int),
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
    parent_record_id: Option(Int),
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

// PACS series result (mirrors backend SeriesResult)
pub type PacsSeriesResult {
  PacsSeriesResult(
    study_instance_uid: String,
    series_instance_uid: String,
    series_number: Option(Int),
    modality: Option(String),
    series_description: Option(String),
    number_of_series_related_instances: Option(Int),
  )
}

// PACS study result (mirrors backend StudyResult)
pub type PacsStudyResult {
  PacsStudyResult(
    patient_id: Option(String),
    patient_name: Option(String),
    study_instance_uid: String,
    study_date: Option(String),
    study_time: Option(String),
    study_description: Option(String),
    accession_number: Option(String),
    modalities_in_study: Option(String),
    number_of_study_related_series: Option(Int),
    number_of_study_related_instances: Option(Int),
  )
}

// Wrapper: study + series + DB existence flag (mirrors backend PacsStudyWithSeries)
pub type PacsStudyWithSeries {
  PacsStudyWithSeries(
    study: PacsStudyResult,
    series: List(PacsSeriesResult),
    already_exists: Bool,
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

// User with role assignments for role matrix
pub type UserRoleInfo {
  UserRoleInfo(
    id: String,
    email: String,
    is_active: Bool,
    is_superuser: Bool,
    role_names: List(String),
  )
}

// Role matrix: all roles and all users with their assignments
pub type RoleMatrix {
  RoleMatrix(roles: List(String), users: List(UserRoleInfo))
}

// Per-status record counts for a record type
pub type RecordTypeStatusCounts {
  RecordTypeStatusCounts(
    preparing: Int,
    blocked: Int,
    pending: Int,
    inwork: Int,
    finished: Int,
    failed: Int,
    pause: Int,
  )
}

// Custom SQL report template (matches backend ReportTemplate)
pub type ReportTemplate {
  ReportTemplate(name: String, title: String, description: String)
}

// Quarto report template (matches backend QuartoReportTemplate)
pub type QuartoReportTemplate {
  QuartoReportTemplate(
    name: String,
    title: String,
    description: String,
    data_reports: List(String),
  )
}

// Quarto render state — subset of the backend status.json sidecar. A single
// render targets one format, so `status == "done"` means that file is ready.
pub type QuartoRenderState {
  QuartoRenderState(render_id: String, status: String, error: Option(String))
}

// Record type with aggregate statistics
pub type RecordTypeStats {
  RecordTypeStats(
    name: String,
    description: Option(String),
    label: Option(String),
    level: String,
    role_name: Option(String),
    min_records: Option(Int),
    max_records: Option(Int),
    total_records: Int,
    records_by_status: RecordTypeStatusCounts,
    unique_users: Int,
  )
}

/// Distinct patient/record_type/user values returned by
/// `POST /api/records/filter-options`. The `users` list is prefixed with
/// `"__unassigned__"` when scope contains any record with no assigned
/// user — matches the literal in `utils/record_filters.unassigned_user_value`.
pub type RecordFilterOptions {
  RecordFilterOptions(
    patients: List(String),
    record_types: List(String),
    users: List(String),
  )
}

/// Record audit event (mirrors backend RecordEventRead). `actor_name` is the
/// acting user's email resolved server-side; None marks a system action.
pub type RecordEvent {
  RecordEvent(
    id: Int,
    record_id: Option(Int),
    record_key: Option(Int),
    record_type_name: Option(String),
    patient_id: Option(String),
    kind: String,
    actor_name: Option(String),
    from_status: Option(String),
    to_status: Option(String),
    reason: Option(String),
    occurred_at: String,
  )
}

/// Pipeline task run audit row (mirrors backend PipelineTaskRunRead).
pub type PipelineRun {
  PipelineRun(
    id: String,
    task_name: String,
    queue: String,
    status: String,
    record_id: Option(Int),
    patient_id: Option(String),
    started_at: String,
    finished_at: Option(String),
    execution_time: Option(Float),
    retry_count: Int,
    error_type: Option(String),
    error_message: Option(String),
  )
}
