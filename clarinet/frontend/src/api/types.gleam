// Core types for the API layer

import gleam/dict.{type Dict}

// Single field-level validation error from the backend.
// Mirrors clarinet.exceptions.domain.FieldError:
//   - path: JSON Pointer "/mappings/2/new_id" (or "" for the document root)
//   - message: localized human-readable text (authored by the validator)
//   - code: machine-readable tag (e.g. "duplicate", "minimum", "required")
pub type FieldError {
  FieldError(path: String, message: String, code: String)
}

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
  // 422 with an `errors` array (RecordDataValidationError handler).
  // Empty list = 400-style placeholder (legacy code path in http_client).
  ValidationError(errors: List(FieldError))
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
