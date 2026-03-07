/// Utility for converting Dynamic values to JSON strings.
///
/// On the JavaScript target, Dynamic values from JSON parsing are plain JS objects.
/// This module provides a bridge to stringify them back to JSON strings.
import gleam/dynamic.{type Dynamic}

/// Convert a Dynamic value (from JSON parsing) back to a JSON string.
@external(javascript, "../json_ffi.mjs", "stringify")
pub fn dynamic_to_string(value: Dynamic) -> String
