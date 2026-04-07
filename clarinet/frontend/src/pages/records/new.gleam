// New record creation page — self-contained MVU module
import api/models.{type Record, type Series, type Study}
import api/patients
import api/records
import api/studies
import api/types.{type ApiError, AuthError}
import components/forms/record_form
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise
import gleam/option.{None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import router
import shared.{type OutMsg, type Shared}

// --- Model ---

pub type Model {
  Model(
    form_data: record_form.RecordFormData,
    form_errors: Dict(String, String),
    form_studies: List(Study),
    form_series: List(Series),
    loading: Bool,
    // Race guards: each cascading load increments these counters and the
    // result handler discards stale responses whose request_id doesn't match
    // the current value. Without this, quickly switching the patient or study
    // can let an older response overwrite the latest one.
    studies_request_id: Int,
    series_request_id: Int,
  )
}

// --- Msg ---

pub type Msg {
  UpdateForm(record_form.RecordFormMsg)
  Submit
  SubmitResult(Result(Record, ApiError))
  StudiesLoaded(request_id: Int, result: Result(List(Study), ApiError))
  SeriesLoaded(request_id: Int, result: Result(List(Series), ApiError))
  Cancel
}

// --- Init ---

pub fn init(shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      form_data: record_form.init(),
      form_errors: dict.new(),
      form_studies: [],
      form_series: [],
      loading: False,
      studies_request_id: 0,
      series_request_id: 0,
    )
  // ReloadPatients is required even for non-superusers — the form needs the
  // patient picker. The backend is expected to scope `/api/patients` results
  // by the caller's permissions.
  let out_msgs = case shared.user {
    Some(models.User(is_superuser: True, ..)) ->
      [shared.ReloadPatients, shared.ReloadRecordTypes, shared.ReloadUsers, shared.ReloadRecords]
    _ ->
      [shared.ReloadPatients, shared.ReloadRecordTypes, shared.ReloadRecords]
  }
  #(model, effect.none(), out_msgs)
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    UpdateForm(form_msg) -> {
      let old_data = model.form_data
      let new_data = record_form.update(old_data, form_msg)
      let updated_model =
        Model(..model, form_data: new_data)

      // Cascade: patient changed → load studies
      let patient_changed = old_data.patient_id != new_data.patient_id
      let study_changed = old_data.study_uid != new_data.study_uid
      let type_changed = old_data.record_type_name != new_data.record_type_name

      // Reset studies/series on patient or type change
      let updated_model = case patient_changed || type_changed {
        True ->
          Model(..updated_model, form_studies: [], form_series: [])
        False -> updated_model
      }

      // Reset series on study change
      let updated_model = case study_changed {
        True -> Model(..updated_model, form_series: [])
        False -> updated_model
      }

      // Determine whether we need to (re)load studies for the current patient.
      let needs_studies_load = case patient_changed, type_changed {
        True, _ -> new_data.patient_id != ""
        False, True -> new_data.patient_id != ""
        False, False -> False
      }

      let #(updated_model, studies_eff) = case needs_studies_load {
        True -> {
          let new_id = updated_model.studies_request_id + 1
          let m = Model(..updated_model, studies_request_id: new_id)
          #(m, load_studies_for_patient(new_id, new_data.patient_id))
        }
        False -> #(updated_model, effect.none())
      }

      // Load series when study selected/changed
      let needs_series_load = study_changed && new_data.study_uid != ""
      let #(updated_model, series_eff) = case needs_series_load {
        True -> {
          let new_id = updated_model.series_request_id + 1
          let m = Model(..updated_model, series_request_id: new_id)
          #(m, load_series_for_study(new_id, new_data.study_uid))
        }
        False -> #(updated_model, effect.none())
      }

      #(updated_model, effect.batch([studies_eff, series_eff]), [])
    }

    StudiesLoaded(request_id, Ok(studies_list)) ->
      case request_id == model.studies_request_id {
        True ->
          #(Model(..model, form_studies: studies_list), effect.none(), [])
        False -> #(model, effect.none(), [])
      }

    StudiesLoaded(request_id, Error(_)) ->
      case request_id == model.studies_request_id {
        True -> #(Model(..model, form_studies: []), effect.none(), [])
        False -> #(model, effect.none(), [])
      }

    SeriesLoaded(request_id, Ok(series_list)) ->
      case request_id == model.series_request_id {
        True ->
          #(Model(..model, form_series: series_list), effect.none(), [])
        False -> #(model, effect.none(), [])
      }

    SeriesLoaded(request_id, Error(_)) ->
      case request_id == model.series_request_id {
        True -> #(Model(..model, form_series: []), effect.none(), [])
        False -> #(model, effect.none(), [])
      }

    Submit -> {
      case record_form.validate(model.form_data, shared.cache.record_types) {
        Ok(_) -> {
          let data = model.form_data
          let record_create = models.RecordCreate(
            record_type_name: data.record_type_name,
            patient_id: data.patient_id,
            status: types.Pending,
            study_uid: optional_string(data.study_uid),
            series_uid: optional_string(data.series_uid),
            user_id: optional_string(data.user_id),
            parent_record_id: case data.parent_record_id {
              "" -> None
              v -> case int.parse(v) {
                Ok(id) -> Some(id)
                Error(_) -> None
              }
            },
            context_info: optional_string(data.context_info),
          )
          let eff = {
            use dispatch <- effect.from
            records.create_record(record_create)
            |> promise.tap(fn(result) { dispatch(SubmitResult(result)) })
            Nil
          }
          #(
            Model(..model, loading: True, form_errors: dict.new()),
            eff,
            [],
          )
        }
        Error(errors) ->
          #(Model(..model, form_errors: errors), effect.none(), [])
      }
    }

    SubmitResult(Ok(record)) -> {
      let route = case record.id {
        Some(id) -> router.RecordDetail(int.to_string(id))
        None -> router.Records
      }
      #(Model(..model, loading: False), effect.none(), [
        shared.CacheRecord(record),
        shared.ShowSuccess("Record created successfully"),
        shared.Navigate(route),
      ])
    }

    SubmitResult(Error(err)) ->
      #(
        Model(..model, loading: False),
        effect.none(),
        handle_error(err, "Failed to create record"),
      )

    Cancel ->
      #(model, effect.none(), [shared.Navigate(router.Records)])
  }
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.ShowError(fallback_msg)]
  }
}

fn optional_string(s: String) -> option.Option(String) {
  case s {
    "" -> None
    v -> Some(v)
  }
}

fn load_studies_for_patient(request_id: Int, patient_id: String) -> Effect(Msg) {
  use dispatch <- effect.from
  patients.get_patient(patient_id)
  |> promise.tap(fn(result) {
    let studies_result = case result {
      Ok(patient) ->
        case patient.studies {
          Some(s) -> Ok(s)
          None -> Ok([])
        }
      Error(err) -> Error(err)
    }
    dispatch(StudiesLoaded(request_id, studies_result))
  })
  Nil
}

fn load_series_for_study(request_id: Int, study_uid: String) -> Effect(Msg) {
  use dispatch <- effect.from
  studies.get_study(study_uid)
  |> promise.tap(fn(result) {
    let series_result = case result {
      Ok(study) ->
        case study.series {
          Some(series_list) -> Ok(series_list)
          None -> Ok([])
        }
      Error(err) -> Error(err)
    }
    dispatch(SeriesLoaded(request_id, series_result))
  })
  Nil
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  html.div([attribute.class("container")], [
    html.h1([], [html.text("New Record")]),
    record_form.view(
      data: model.form_data,
      studies: model.form_studies,
      series_list: model.form_series,
      errors: model.form_errors,
      loading: model.loading,
      shared: shared,
      on_update: fn(msg) { UpdateForm(msg) },
      on_submit: fn() { Submit },
      on_cancel: Cancel,
    ),
  ])
}
