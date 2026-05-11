// Core types for the API layer

import gleam/dict.{type Dict}

// API Error types
pub type ApiError {
  NetworkError(String)
  ParseError(String)
  AuthError(String)
  ServerError(code: Int, message: String)
  // Structured server error carrying a machine-readable code and optional
  // metadata. Emitted on any 4xx response whose body contains a `code`
  // field — not limited to 409 (e.g. a future 422 with `code` would land
  // here too). Currently produced by EntityAlreadyExistsError handler and
  // record-quota errors (RECORD_LIMIT_REACHED / UNIQUE_PER_USER).
  StructuredError(
    error_code: String,
    message: String,
    metadata: Dict(String, String),
  )
  ValidationError(errors: List(#(String, String)))
}

// Record status (matching backend RecordStatus enum)
pub type RecordStatus {
  Blocked
  // blocked in backend
  Pending
  // pending in backend
  InWork
  // inwork in backend
  Finished
  // finished in backend
  Failed
  // failed in backend
  Paused
  // pause in backend
}

// DicomQueryLevel for RecordType
pub type DicomQueryLevel {
  Patient
  Study
  Series
}
