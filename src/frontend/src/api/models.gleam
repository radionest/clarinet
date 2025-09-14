// Static type definitions for core models
import gleam/option.{type Option}
import gleam/json.{type Json}
import api/types.{type Gender, type UserRole, type TaskStatus}

// Patient model
pub type Patient {
  Patient(
    id: Option(Int),
    name: String,
    birth_date: String,
    medical_record: String,
    gender: Gender,
    notes: Option(String),
    created_at: Option(String),
    updated_at: Option(String),
  )
}

// Study model
pub type Study {
  Study(
    id: Option(Int),
    patient_id: Int,
    patient: Option(Patient),
    modality: String,
    description: String,
    study_date: String,
    institution: String,
    series_count: Int,
    file_count: Int,
    size_mb: Float,
    created_at: Option(String),
    updated_at: Option(String),
  )
}

// Task Design model
pub type TaskDesign {
  TaskDesign(
    id: Option(Int),
    name: String,
    description: String,
    category: String,
    result_schema: Json,  // JSON Schema for dynamic form
    is_active: Bool,
    version: String,
    created_at: Option(String),
    updated_at: Option(String),
  )
}

// Task model
pub type Task {
  Task(
    id: Option(Int),
    design_id: Int,
    design: Option(TaskDesign),
    study_id: Option(Int),
    study: Option(Study),
    user_id: Int,
    status: TaskStatus,
    result: Option(Json),  // Dynamic result based on design schema
    error_message: Option(String),
    started_at: Option(String),
    completed_at: Option(String),
    created_at: Option(String),
    updated_at: Option(String),
  )
}

// User model
pub type User {
  User(
    id: Int,
    username: String,
    email: String,
    full_name: Option(String),
    role: UserRole,
    is_active: Bool,
    created_at: Option(String),
    last_login: Option(String),
  )
}

// Series model for Study details
pub type Series {
  Series(
    id: Int,
    study_id: Int,
    series_number: Int,
    modality: String,
    description: String,
    body_part: Option(String),
    instance_count: Int,
    created_at: Option(String),
  )
}

// Authentication models
pub type LoginRequest {
  LoginRequest(
    username: String,
    password: String,
  )
}

pub type LoginResponse {
  LoginResponse(
    access_token: String,
    token_type: String,
    user: User,
  )
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
pub type PatientForm {
  PatientForm(
    name: String,
    birth_date: String,
    medical_record: String,
    gender: Gender,
    notes: Option(String),
  )
}

pub type StudyForm {
  StudyForm(
    patient_id: Int,
    modality: String,
    description: String,
    study_date: String,
    institution: String,
  )
}

pub type TaskDesignForm {
  TaskDesignForm(
    name: String,
    description: String,
    category: String,
    result_schema: Json,
    is_active: Bool,
  )
}

pub type UserForm {
  UserForm(
    username: String,
    email: String,
    full_name: Option(String),
    role: UserRole,
    is_active: Bool,
  )
}