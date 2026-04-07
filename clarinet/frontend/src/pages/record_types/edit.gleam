// Record type edit page (admin only) — self-contained MVU module using Formosh
import api/models.{type FileDefinition, type RecordType}
import api/records
import api/types
import config
import gleam/javascript/promise
import formosh/component as formosh_component
import gleam/dict
import gleam/dynamic/decode
import gleam/json
import gleam/list
import gleam/option.{None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import router
import shared.{type OutMsg, type Shared}

// --- Model ---

pub type Model {
  Model(name: String)
}

// --- Msg ---

pub type Msg {
  RecordTypeLoaded(Result(RecordType, types.ApiError))
  FormSubmitSuccess(name: String)
  FormSubmitError(error: String)
}

// --- Init ---

pub fn init(name: String, _shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let eff = {
    use dispatch <- effect.from
    records.get_record_type(name)
    |> promise.tap(fn(result) { dispatch(RecordTypeLoaded(result)) })
    Nil
  }
  #(Model(name: name), eff, [])
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    RecordTypeLoaded(Ok(rt)) ->
      #(model, effect.none(), [shared.CacheRecordType(rt)])
    RecordTypeLoaded(Error(_)) ->
      #(model, effect.none(), [shared.ShowError("Failed to load record type")])
    FormSubmitSuccess(name) ->
      #(model, effect.none(), [
        shared.ShowSuccess("Record type updated successfully"),
        shared.Navigate(router.AdminRecordTypeDetail(name)),
        shared.ReloadRecordTypeStats,
      ])
    FormSubmitError(error) ->
      #(model, effect.none(), [shared.ShowError(error)])
  }
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  case dict.get(shared.cache.record_types, model.name) {
    Ok(rt) -> render_edit(rt, model.name)
    Error(_) -> loading_view(model.name)
  }
}

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
    \"min_records\": {
      \"type\": \"integer\",
      \"title\": \"Min Records\",
      \"minimum\": 0
    },
    \"max_records\": {
      \"type\": \"integer\",
      \"title\": \"Max Records\",
      \"minimum\": 1
    },
    \"unique_per_user\": {
      \"type\": \"boolean\",
      \"title\": \"Unique Per User\",
      \"default\": false
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
    \"file_registry\": {
      \"type\": \"array\",
      \"title\": \"File Registry\",
      \"items\": {
        \"type\": \"object\",
        \"properties\": {
          \"name\": { \"type\": \"string\", \"title\": \"Name\" },
          \"pattern\": { \"type\": \"string\", \"title\": \"Pattern\" },
          \"description\": { \"type\": \"string\", \"title\": \"Description\" },
          \"required\": { \"type\": \"boolean\", \"title\": \"Required\", \"default\": true },
          \"multiple\": { \"type\": \"boolean\", \"title\": \"Multiple (collection)\", \"default\": false },
          \"role\": { \"type\": \"string\", \"title\": \"Role\", \"enum\": [\"input\", \"output\", \"intermediate\"], \"default\": \"output\" }
        },
        \"required\": [\"name\", \"pattern\"]
      }
    }
  }
}"

fn render_edit(rt: RecordType, name: String) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [
        html.text("Edit Record Type: " <> option.unwrap(rt.label, rt.name)),
      ]),
      html.a(
        [
          attribute.href(
            router.route_to_path(router.AdminRecordTypeDetail(name)),
          ),
          attribute.class("btn btn-secondary"),
        ],
        [html.text("Back to Details")],
      ),
    ]),
    html.div([attribute.class("card")], [
      formosh_component.element([
        formosh_component.schema_string(record_type_edit_schema),
        formosh_component.submit_url(
          config.base_path() <> "/api/records/types/" <> name,
        ),
        formosh_component.submit_method("PATCH"),
        formosh_component.initial_values_string(build_initial_values(rt)),
        event.on("formosh-submit", decode_edit_submit(name)),
      ]),
    ]),
  ])
}

fn decode_edit_submit(name: String) -> decode.Decoder(Msg) {
  use status <- decode.then(decode.at(["detail", "status"], decode.string))
  case status {
    "success" -> decode.success(FormSubmitSuccess(name))
    _ -> decode.success(FormSubmitError("Update failed"))
  }
}

fn build_initial_values(rt: RecordType) -> String {
  let fields = [
    #("description", json.nullable(rt.description, json.string)),
    #("label", json.nullable(rt.label, json.string)),
    #("level", level_to_json(rt.level)),
    #("role_name", json.nullable(rt.role_name, json.string)),
    #("min_records", json.nullable(rt.min_records, json.int)),
    #("max_records", json.nullable(rt.max_records, json.int)),
    #("unique_per_user", json.bool(rt.unique_per_user)),
    #("slicer_script", json.nullable(rt.slicer_script, json.string)),
    #(
      "slicer_result_validator",
      json.nullable(rt.slicer_result_validator, json.string),
    ),
    #("slicer_script_args", dict_to_json_string(rt.slicer_script_args)),
    #(
      "slicer_result_validator_args",
      dict_to_json_string(rt.slicer_result_validator_args),
    ),
    #("data_schema", json.nullable(rt.data_schema, json.string)),
    #("file_registry", file_definitions_to_json(rt.file_registry)),
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

fn dict_to_json_string(
  d: option.Option(dict.Dict(String, String)),
) -> json.Json {
  case d {
    Some(dict_val) -> {
      let entries =
        dict.to_list(dict_val)
        |> list.map(fn(pair) { #(pair.0, json.string(pair.1)) })
        |> json.object
      json.string(json.to_string(entries))
    }
    None -> json.null()
  }
}

fn file_definitions_to_json(
  files: option.Option(List(FileDefinition)),
) -> json.Json {
  case files {
    Some(file_list) ->
      json.array(file_list, fn(f) {
        let role_str = case f.role {
          models.Input -> "input"
          models.Output -> "output"
          models.Intermediate -> "intermediate"
        }
        json.object([
          #("name", json.string(f.name)),
          #("pattern", json.string(f.pattern)),
          #("description", json.nullable(f.description, json.string)),
          #("required", json.bool(f.required)),
          #("multiple", json.bool(f.multiple)),
          #("role", json.string(role_str)),
        ])
      })
    None -> json.preprocessed_array([])
  }
}

fn loading_view(name: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading record type " <> name <> "...")]),
  ])
}
