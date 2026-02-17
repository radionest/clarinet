// Core types for the API layer
import gleam/option.{type Option}

// API Error types
pub type ApiError {
  NetworkError(String)
  ParseError(String)
  AuthError(String)
  ServerError(code: Int, message: String)
  ValidationError(errors: List(#(String, String)))
}

// User role types
pub type UserRole {
  Admin
  User
  Viewer
}

// Record status (matching backend RecordStatus enum)
pub type RecordStatus {
  Pending
  // pending in backend
  InWork
  // inwork in backend
  Finished
  // finished in backend
  Failed
  // failed in backend
  Cancelled
  // cancelled in backend
  Paused
  // pause in backend
}

// DicomQueryLevel for RecordType
pub type DicomQueryLevel {
  Patient
  Study
  Series
}

// Generic response wrapper
pub type ApiResponse(a) {
  Success(data: a)
  Error(error: ApiError)
}

// Pagination info
pub type Pagination {
  Pagination(page: Int, per_page: Int, total: Int, total_pages: Int)
}

// List response with pagination
pub type ListResponse(a) {
  ListResponse(items: List(a), pagination: Pagination)
}
