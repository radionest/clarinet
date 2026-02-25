// Record type edit page (admin only) using Formosh web component
import api/models.{type FileDefinition, type RecordType}
import api/types
import formosh/component as formosh_component
import gleam/dict
import gleam/dynamic/decode
import gleam/json
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import store.{type Model, type Msg}

/// JSON Schema for the RecordType edit form
const record_type_edit_schema = "{
  \"type\": \"object\",
  \"properties\": {
    \"description\": {
      \"type\": \"string\",
      \"title\": \"Description\",
      \"maxLength\": 500
    },
    \"label\": {
      \"type\": \"string\",
      \"title\": \"Label\",
      \"maxLength\": 100
    },
    \"level\": {
      \"type\": \"string\",
      \"title\": \"Level\",
      \"enum\": [\"PATIENT\", \"STUDY\", \"SERIES\"]
    },
    \"role_name\": {
      \"type\": \"string\",
      \"title\": \"Role Name\"
    },
    \"min_users\": {
      \"type\": \"integer\",
      \"title\": \"Min Users\",
      \"minimum\": 0
    },
    \"max_users\": {
      \"type\": \"integer\",
      \"title\": \"Max Users\",
      \"minimum\": 1
    },
    \"slicer_script\": {
      \"type\": \"string\",
      \"title\": \"Slicer Script\"
    },
    \"slicer_result_validator\": {
      \"type\": \"string\",
      \"title\": \"Slicer Result Validator\"
    },
    \"slicer_script_args\": {
      \"type\": \"string\",
      \"title\": \"Slicer Script Args (JSON)\",
      \"maxLength\": 5000
    },
    \"slicer_result_validator_args\": {
      \"type\": \"string\",
      \"title\": \"Slicer Result Validator Args (JSON)\",
      \"maxLength\": 5000
    },
    \"data_schema\": {
      \"type\": \"string\",
      \"title\": \"Data Schema (JSON)\",
      \"maxLength\": 50000
    },
    \"input_files\": {
      \"type\": \"array\",
      \"title\": \"Input Files\",
      \"items\": {
        \"type\": \"object\",
        \"properties\": {
          \"name\": { \"type\": \"string\", \"title\": \"Name\" },
          \"pattern\": { \"type\": \"string\", \"title\": \"Pattern\" },
          \"description\": { \"type\": \"string\", \"title\": \"Description\" },
          \"required\": { \"type\": \"boolean\", \"title\": \"Required\", \"default\": true }
        },
        \"required\": [\"name\", \"pattern\"]
      }
    },
    \"output_files\": {
      \"type\": \"array\",
      \"title\": \"Output Files\",
      \"items\": {
        \"type\": \"object\",
        \"properties\": {
          \"name\": { \"type\": \"string\", \"title\": \"Name\" },
          \"pattern\": { \"type\": \"string\", \"title\": \"Pattern\" },
          \"description\": { \"type\": \"string\", \"title\": \"Description\" },
          \"required\": { \"type\": \"boolean\", \"title\": \"Required\", \"default\": true }
        },
        \"required\": [\"name\", \"pattern\"]
      }
    }
  }
}"

/// Build initial values JSON string from a RecordType
fn build_initial_values(rt: RecordType) -> String {
  let fields = [
    #("description", json.nullable(rt.description, json.string)),
    #("label", json.nullable(rt.label, json.string)),
    #("level", level_to_json(rt.level)),
    #("role_name", json.nullable(rt.role_name, json.string)),
    #("min_users", json.nullable(rt.min_users, json.int)),
    #("max_users", json.nullable(rt.max_users, json.int)),
    #("slicer_script", json.nullable(rt.slicer_script, json.string)),
    #("slicer_result_validator", json.nullable(rt.slicer_result_validator, json.string)),
    #("slicer_script_args", dict_to_json_string(rt.slicer_script_args)),
    #("slicer_result_validator_args", dict_to_json_string(rt.slicer_result_validator_args)),
    #("data_schema", json.nullable(rt.data_schema, json.string)),
    #("input_files", file_definitions_to_json(rt.input_files)),
    #("output_files", file_definitions_to_json(rt.output_files)),
  ]
  json.to_string(json.object(fields))
}

fn level_to_json(level: types.DicomQueryLevel) -> json.Json {
  case level {
    types.Patient -> json.string("PATIENT")
    types.Study -> json.string("STUDY")
    types.Series -> json.string("SERIES")
  }
}

/// Convert an optional dict to a JSON string value (for textarea fields)
fn dict_to_json_string(
  d: option.Option(dict.Dict(String, String)),
) -> json.Json {
  case d {
    Some(dict_val) -> {
      let entries =
        dict.to_list(dict_val)
        |> list.map(fn(pair) { #(pair.0, json.string(pair.1)) })
        |> json.object
      // Stringify the dict to a JSON string for the textarea
      json.string(json.to_string(entries))
    }
    None -> json.null()
  }
}

/// Convert optional list of FileDefinitions to JSON array
fn file_definitions_to_json(
  files: option.Option(List(FileDefinition)),
) -> json.Json {
  case files {
    Some(file_list) ->
      json.array(file_list, fn(f) {
        json.object([
          #("name", json.string(f.name)),
          #("pattern", json.string(f.pattern)),
          #("description", json.nullable(f.description, json.string)),
          #("required", json.bool(f.required)),
        ])
      })
    None -> json.preprocessed_array([])
  }
}

/// View for the edit page
pub fn view(model: Model, name: String) -> Element(Msg) {
  case dict.get(model.record_types, name) {
    Ok(rt) -> render_edit(model, rt, name)
    Error(_) -> loading_view(name)
  }
}

fn render_edit(_model: Model, rt: RecordType, name: String) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [
        html.text("Edit Record Type: " <> option.unwrap(rt.label, rt.name)),
      ]),
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(store.Navigate(router.AdminRecordTypeDetail(name))),
        ],
        [html.text("Back to Details")],
      ),
    ]),
    html.div([attribute.class("card")], [
      formosh_component.element([
        formosh_component.schema_string(record_type_edit_schema),
        formosh_component.submit_url("/api/records/types/" <> name),
        formosh_component.submit_method("PATCH"),
        formosh_component.initial_values_string(build_initial_values(rt)),
        event.on("formosh-submit", decode_edit_submit(name)),
      ]),
    ]),
  ])
}

/// Decode formosh-submit custom event for the edit form
fn decode_edit_submit(name: String) -> decode.Decoder(Msg) {
  use status <- decode.then(decode.at(["detail", "status"], decode.string))
  case status {
    "success" -> decode.success(store.RecordTypeEditSuccess(name))
    _ -> decode.success(store.RecordTypeEditError("Update failed"))
  }
}

fn loading_view(name: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading record type " <> name <> "...")]),
  ])
}
