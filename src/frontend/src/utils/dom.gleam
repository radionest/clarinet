// Native DOM utilities using plinth
import gleam/dynamic
import gleam/option.{type Option, None, Some}
import plinth/browser/document
import plinth/browser/element
import plinth/browser/window

// Get the value of an input element by its ID
pub fn get_input_value(id: String) -> Option(String) {
  case document.query_selector("#" <> id) {
    Ok(el) -> {
      // Get the value property from the element
      case element.value(el) {
        Ok(val) -> Some(val)
        Error(_) -> None
      }
    }
    Error(_) -> None
  }
}

// Set the value of an input element by its ID
pub fn set_input_value(id: String, value: String) -> Nil {
  case document.query_selector("#" <> id) {
    Ok(el) -> {
      element.set_value(el, value)
      Nil
    }
    Error(_) -> Nil
  }
}

// Focus an element by its ID
pub fn focus_element(id: String) -> Nil {
  case document.query_selector("#" <> id) {
    Ok(el) -> {
      element.focus(el)
      Nil
    }
    Error(_) -> Nil
  }
}

// Check if we're in development mode (localhost)
pub fn is_development() -> Bool {
  let url = window.location()
  // Check if URL contains localhost or development indicators
  case url {
    "http://localhost" <> _ -> True
    "http://127.0.0.1" <> _ -> True
    "http://0.0.0.0" <> _ -> True
    _ -> False
  }
}
