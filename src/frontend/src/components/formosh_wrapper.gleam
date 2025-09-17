// Wrapper for Formosh dynamic form library (Gleam library)
// Formosh generates forms from JSON Schema for Task.result
import lustre/element.{type Element}
import lustre/element/html
import lustre/attribute
import lustre/event
import lustre/effect.{type Effect}
import gleam/option.{type Option, None, Some}
import gleam/json.{type Json}
import gleam/dict.{type Dict}
import gleam/list
import gleam/dynamic.{type Dynamic}
import gleam/dynamic/decode
import gleam/result
import gleam/string
import store.{type Msg}

// Import Formosh library modules
import formosh
import form/model as form_model
import form/update as form_update
import form/view as form_view
import schema/types as schema_types
import schema/parser

// Type for form submission data - using FieldValue from schema_types
pub type FormoshData = Dict(String, schema_types.FieldValue)

// Wrapper type to hold form state
pub type FormoshWrapper {
  FormoshWrapper(
    model: form_model.FormModel,
    on_submit: fn(FormoshData) -> Msg,
    on_change: Option(fn(FormoshData) -> Msg),
  )
}

// Render dynamic form using JSON Schema
pub fn render_task_form(
  schema: schema_types.JsonSchema,
  initial_data: Option(FormoshData),
  on_submit: fn(FormoshData) -> Msg,
  on_change: Option(fn(FormoshData) -> Msg),
  read_only: Bool,
) -> Element(Msg) {
  // Initialize the form model
  let form_model = case initial_data {
    Some(data) -> {
      let model = form_model.init(schema)
      form_model.FormModel(..model, values: data)
    }
    None -> form_model.init(schema)
  }

  // Render the form using Formosh's view function
  let form_element = form_view.view(form_model)
    |> element.map(fn(form_msg) {
      // Convert FormMsg to our app's Msg
      handle_form_msg(form_msg, form_model, on_submit, on_change)
    })

  html.div(
    [
      attribute.class("formosh-container"),
      attribute.id("task-result-form"),
    ],
    [
      case read_only {
        True -> with_readonly(form_element, True)
        False -> form_element
      },
    ],
  )
}

// Helper to handle form messages and convert to app messages
fn handle_form_msg(
  form_msg: form_model.FormMsg,
  model: form_model.FormModel,
  on_submit: fn(FormoshData) -> Msg,
  on_change: Option(fn(FormoshData) -> Msg),
) -> Msg {
  case form_msg {
    form_model.FormSubmit -> {
      // Convert form values to FormoshData and call on_submit
      on_submit(model.values)
    }
    form_model.UpdateFieldPath(_, _) | form_model.AddArrayItemPath(_)
    | form_model.RemoveArrayItemPath(_, _) | form_model.ValidateForm -> {
      // Handle field updates
      case on_change {
        Some(handler) -> handler(model.values)
        None -> store.NoOp
      }
    }
    _ -> store.NoOp
  }
}

// Apply readonly state to form
fn with_readonly(form: Element(msg), read_only: Bool) -> Element(msg) {
  case read_only {
    True -> html.div([attribute.class("readonly-form")], [form])
    False -> form
  }
}

// Render a preview-only form (no submit)
pub fn render_preview(
  schema: schema_types.JsonSchema,
  sample_data: Option(FormoshData),
) -> Element(Msg) {
  html.div(
    [
      attribute.class("formosh-preview"),
      attribute.id("form-preview"),
    ],
    [
      html.div([attribute.class("preview-header")], [
        html.h4([], [html.text("Form Preview")]),
        html.p([attribute.class("preview-help")], [
          html.text("This is how the form will appear to users"),
        ]),
      ]),
      render_task_form(
        schema,
        sample_data,
        fn(_) { store.NoOp },  // No-op for preview
        None,
        True,  // Preview is read-only
      ),
    ],
  )
}

// Render form with validation state
pub fn render_with_validation(
  schema: schema_types.JsonSchema,
  data: FormoshData,
  errors: List(String),
  on_submit: fn(FormoshData) -> Msg,
  on_change: fn(FormoshData) -> Msg,
) -> Element(Msg) {
  html.div(
    [attribute.class("formosh-validated")],
    [
      // Show validation errors if any
      case errors {
        [] -> html.text("")
        errs -> html.div([attribute.class("validation-errors")],
          list.map(errs, fn(error) {
            html.div([attribute.class("validation-error")], [
              html.text(error),
            ])
          })
        )
      },
      // Render the form
      render_task_form(
        schema,
        Some(data),
        on_submit,
        Some(on_change),
        False,
      ),
    ],
  )
}

// Helper to create a simple JSON Schema for testing
pub fn sample_schema() -> schema_types.JsonSchema {
  schema_types.JsonSchema(
    title: "Sample Task Result",
    description: Some("A sample form for task results"),
    field_type: schema_types.ObjectType,
    properties: dict.from_list([
      #("findings", schema_types.SchemaProperty(
        field_type: Some(schema_types.StringType),
        title: Some("Findings"),
        description: Some("Describe your findings"),
        default: None,
        enum_values: None,
        ref: None,
        string_constraints: Some(schema_types.StringConstraints(
          min_length: Some(1),
          max_length: Some(500),
          pattern: None,
          format: None,
        )),
        number_constraints: None,
        items: None,
        properties: None,
        required: [],
      )),
      #("severity", schema_types.SchemaProperty(
        field_type: Some(schema_types.StringType),
        title: Some("Severity"),
        description: Some("Select severity level"),
        default: Some(schema_types.JsonString("normal")),
        enum_values: Some([
          schema_types.JsonString("normal"),
          schema_types.JsonString("mild"),
          schema_types.JsonString("moderate"),
          schema_types.JsonString("severe"),
        ]),
        ref: None,
        string_constraints: None,
        number_constraints: None,
        items: None,
        properties: None,
        required: [],
      )),
      #("notes", schema_types.SchemaProperty(
        field_type: Some(schema_types.StringType),
        title: Some("Additional Notes"),
        description: None,
        default: None,
        enum_values: None,
        ref: None,
        string_constraints: Some(schema_types.empty_string_constraints()),
        number_constraints: None,
        items: None,
        properties: None,
        required: [],
      )),
    ]),
    required: ["findings"],
    defs: None,
    conditionals: [],
    string_constraints: None,
    number_constraints: None,
  )
}

// Helper to create a simple JSON object for testing
pub fn sample_schema_json() -> Json {
  json.object([
    #("type", json.string("object")),
    #("title", json.string("Sample Task Result")),
    #("required", json.array([json.string("findings")], fn(x) { x })),
    #("properties", json.object([
      #("findings", json.object([
        #("type", json.string("string")),
        #("title", json.string("Findings")),
        #("description", json.string("Describe your findings")),
      ])),
      #("severity", json.object([
        #("type", json.string("string")),
        #("title", json.string("Severity")),
        #("enum", json.array([
          json.string("normal"),
          json.string("mild"),
          json.string("moderate"),
          json.string("severe"),
        ], fn(x) { x })),
      ])),
      #("notes", json.object([
        #("type", json.string("string")),
        #("title", json.string("Additional Notes")),
      ])),
    ])),
  ])
}

// Loading state wrapper
pub fn with_loading(
  loading: Bool,
  form: Element(Msg),
) -> Element(Msg) {
  html.div([attribute.class("formosh-wrapper")], [
    case loading {
      True -> html.div([attribute.class("formosh-loading")], [
        html.div([attribute.class("spinner")], []),
        html.p([], [html.text("Loading form...")]),
      ])
      False -> form
    },
  ])
}

// Error state wrapper
pub fn with_error(
  error: Option(String),
  form: Element(Msg),
) -> Element(Msg) {
  html.div([attribute.class("formosh-wrapper")], [
    case error {
      Some(msg) -> html.div([attribute.class("formosh-error")], [
        html.p([], [html.text("Error loading form:")]),
        html.p([attribute.class("error-message")], [html.text(msg)]),
      ])
      None -> form
    },
  ])
}

// Wrapper with title and description
pub fn with_header(
  title: String,
  description: Option(String),
  form: Element(Msg),
) -> Element(Msg) {
  html.div([attribute.class("formosh-with-header")], [
    html.div([attribute.class("formosh-header")], [
      html.h3([], [html.text(title)]),
      case description {
        Some(desc) -> html.p([attribute.class("form-description")], [html.text(desc)])
        None -> html.text("")
      },
    ]),
    form,
  ])
}

// Helper to parse JSON Schema string to JsonSchema type
pub fn parse_schema(schema_string: String) -> Result(schema_types.JsonSchema, String) {
  case parser.parse_schema(schema_string) {
    Ok(schema) -> Ok(schema)
    Error(parse_error) -> {
      // Convert ParseError to String for error message
      case parse_error {
        parser.InvalidJson(msg) -> Error("Invalid JSON: " <> msg)
        parser.MissingField(field) -> Error("Missing field: " <> field)
        parser.InvalidType(t) -> Error("Invalid type: " <> t)
        parser.UnexpectedValue(val) -> Error("Unexpected value: " <> val)
        parser.DecodingError(_) -> Error("Failed to decode JSON Schema")
      }
    }
  }
}

// Helper to validate if a JsonSchema is valid
pub fn is_valid_schema(schema: schema_types.JsonSchema) -> Bool {
  // Check that the schema has required fields
  case schema.field_type {
    schema_types.ObjectType -> True
    _ -> False
  }
}

// Helper to validate if a JSON string is a valid JSON Schema
pub fn is_valid_schema_string(schema_string: String) -> Bool {
  case parse_schema(schema_string) {
    Ok(schema) -> is_valid_schema(schema)
    Error(_) -> False
  }
}

// Convert FormoshData (FieldValue dict) to JSON for API submission
pub fn to_json(data: FormoshData) -> Json {
  // Convert Dict(String, FieldValue) to JSON object
  dict.to_list(data)
  |> list.map(fn(pair) {
    let #(key, value) = pair
    #(key, field_value_to_json(value))
  })
  |> json.object
}

// Helper to convert FieldValue to Json
fn field_value_to_json(value: schema_types.FieldValue) -> Json {
  case value {
    schema_types.StringValue(s) -> json.string(s)
    schema_types.NumberValue(f) -> json.float(f)
    schema_types.IntegerValue(i) -> json.int(i)
    schema_types.BooleanValue(b) -> json.bool(b)
    schema_types.NullValue -> json.null()
    schema_types.ArrayValue(items) -> {
      json.array(
        list.map(items, json_value_to_json),
        fn(x) { x }
      )
    }
    schema_types.ObjectValue(props) -> {
      json.object(
        list.map(props, fn(pair) {
          let #(key, val) = pair
          #(key, json_value_to_json(val))
        })
      )
    }
  }
}

// Helper to convert JsonValue to Json
fn json_value_to_json(value: schema_types.JsonValue) -> Json {
  case value {
    schema_types.JsonString(s) -> json.string(s)
    schema_types.JsonNumber(f) -> json.float(f)
    schema_types.JsonInteger(i) -> json.int(i)
    schema_types.JsonBool(b) -> json.bool(b)
    schema_types.JsonNull -> json.null()
    schema_types.JsonArray(items) -> {
      json.array(
        list.map(items, json_value_to_json),
        fn(x) { x }
      )
    }
    schema_types.JsonObject(props) -> {
      json.object(
        list.map(props, fn(pair) {
          let #(key, val) = pair
          #(key, json_value_to_json(val))
        })
      )
    }
  }
}

// Convert JSON to FormoshData for form initialization
pub fn from_json(json_value: Json) -> Result(FormoshData, String) {
  // Convert Json to string then parse with decoder
  let json_str = json.to_string(json_value)

  // Parse JSON string into dynamic
  use dyn <- result.try(
    json.parse(json_str, decode.dynamic)
    |> result.map_error(fn(_) { "Failed to parse JSON" })
  )

  // Decode as a dict
  use json_dict <- result.try(
    decode.run(dyn, decode.dict(decode.string, decode.dynamic))
    |> result.map_error(fn(_) { "JSON must be an object" })
  )

  // Convert each value to FieldValue
  dict.fold(json_dict, dict.new(), fn(acc, key, value) {
    case dynamic_to_field_value(value) {
      Some(field_val) -> dict.insert(acc, key, field_val)
      None -> acc
    }
  })
  |> Ok
}

// Convert JSON dict to FormoshData for form initialization
pub fn from_json_dict(json_data: Dict(String, Json)) -> FormoshData {
  dict.fold(json_data, dict.new(), fn(acc, key, value) {
    // Convert Json to string then parse
    let json_str = json.to_string(value)
    case json.parse(json_str, decode.dynamic) {
      Ok(dyn) -> {
        case dynamic_to_field_value(dyn) {
          Some(field_val) -> dict.insert(acc, key, field_val)
          None -> acc
        }
      }
      Error(_) -> acc
    }
  })
}

// Helper to convert Dynamic to FieldValue
fn dynamic_to_field_value(value: Dynamic) -> Option(schema_types.FieldValue) {
  // Try to decode as different types using decode.run
  case decode.run(value, decode.string) {
    Ok(s) -> Some(schema_types.StringValue(s))
    Error(_) -> {
      case decode.run(value, decode.int) {
        Ok(i) -> Some(schema_types.IntegerValue(i))
        Error(_) -> {
          case decode.run(value, decode.bool) {
            Ok(b) -> Some(schema_types.BooleanValue(b))
            Error(_) -> {
              case decode.run(value, decode.float) {
                Ok(f) -> Some(schema_types.NumberValue(f))
                Error(_) -> {
                  // Try to decode as null
                  case decode.run(value, decode.optional(decode.string)) {
                    Ok(None) -> Some(schema_types.NullValue)
                    _ -> None
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}

// Helper to parse JSON Schema from JSON string
pub fn parse_json_schema(json_str: String) -> Result(schema_types.JsonSchema, String) {
  parser.parse_schema(json_str)
  |> result.map_error(fn(err) {
    case err {
      parser.InvalidJson(msg) -> "Invalid JSON: " <> msg
      parser.MissingField(field) -> "Missing field: " <> field
      parser.InvalidType(t) -> "Invalid type: " <> t
      parser.UnexpectedValue(val) -> "Unexpected value: " <> val
      parser.DecodingError(_) -> "Failed to decode JSON Schema"
    }
  })
}

// Create form configuration from schema
pub fn create_form_config(schema: schema_types.JsonSchema) -> formosh.FormConfig {
  formosh.config(schema)
  |> formosh.with_css_prefix("clarinet-form")
}