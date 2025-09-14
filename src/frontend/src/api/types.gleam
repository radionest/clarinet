// Core types for the API layer
import gleam/option.{type Option}
import gleam/json.{type Json}

// API Configuration
pub type ApiConfig {
  ApiConfig(
    base_url: String,
    token: Option(String),
  )
}

// API Error types
pub type ApiError {
  NetworkError(String)
  ParseError(String)
  AuthError(String)
  ServerError(code: Int, message: String)
  ValidationError(errors: List(#(String, String)))
}

// Gender type for Patient
pub type Gender {
  Male
  Female
  Other
  Unknown
}

// User role types
pub type UserRole {
  Admin
  User
  Viewer
}

// Task status
pub type TaskStatus {
  Pending
  Running
  Completed
  Failed
  Cancelled
}

// Generic response wrapper
pub type ApiResponse(a) {
  Success(data: a)
  Error(error: ApiError)
}

// Pagination info
pub type Pagination {
  Pagination(
    page: Int,
    per_page: Int,
    total: Int,
    total_pages: Int,
  )
}

// List response with pagination
pub type ListResponse(a) {
  ListResponse(
    items: List(a),
    pagination: Pagination,
  )
}