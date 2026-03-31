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
import gleam/list
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
  )
}

// --- Msg ---

pub type Msg {
  UpdateForm(record_form.RecordFormMsg)
  Submit
  SubmitResult(Result(Record, ApiError))
  StudiesLoaded(Result(List(Study), ApiError))
  SeriesLoaded(Result(List(Series), ApiError))
  RecordTypesLoaded(Result(List(models.RecordType), ApiError))
  Cancel
}

// --- Init ---

pub fn init(shared: Shared) -> #(Model, Effect(Msg)) {
  let model =
    Model(
      form_data: record_form.init(),
      form_errors: dict.new(),
      form_studies: [],
      form_series: [],
      loading: False,
    )
  // Load record types if not yet cached
  let load_rt_eff = case dict.is_empty(shared.record_types) {
    True -> {
      use dispatch <- effect.from
      records.get_record_types()
      |> promise.tap(fn(result) { dispatch(RecordTypesLoaded(result)) })
      Nil
    }
    False -> effect.none()
  }
  #(model, load_rt_eff)
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

      // Load studies when patient selected/changed
      let studies_eff = case patient_changed, new_data.patient_id {
        True, "" -> effect.none()
        True, pid -> load_studies_for_patient(pid)
        False, _ ->
          // Also load studies if type changed but patient already selected
          case type_changed, new_data.patient_id {
            True, "" -> effect.none()
            True, pid -> load_studies_for_patient(pid)
            False, _ -> effect.none()
          }
      }

      // Load series when study selected/changed
      let series_eff = case study_changed, new_data.study_uid {
        True, "" -> effect.none()
        True, uid -> load_series_for_study(uid)
        False, _ -> effect.none()
      }

      #(updated_model, effect.batch([studies_eff, series_eff]), [])
    }

    StudiesLoaded(Ok(studies_list)) ->
      #(Model(..model, form_studies: studies_list), effect.none(), [])

    StudiesLoaded(Error(_)) ->
      #(Model(..model, form_studies: []), effect.none(), [])

    SeriesLoaded(Ok(series_list)) ->
      #(Model(..model, form_series: series_list), effect.none(), [])

    SeriesLoaded(Error(_)) ->
      #(Model(..model, form_series: []), effect.none(), [])

    RecordTypesLoaded(Ok(rt_list)) -> {
      let rt_dict =
        dict.from_list(
          rt_list
          |> list.map(fn(rt) { #(rt.name, rt) }),
        )
      #(model, effect.none(), list.map(dict.values(rt_dict), shared.CacheRecordType))
    }

    RecordTypesLoaded(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to load record types"))

    Submit -> {
      case record_form.validate(model.form_data, shared.record_types) {
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

fn load_studies_for_patient(patient_id: String) -> Effect(Msg) {
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
    dispatch(StudiesLoaded(studies_result))
  })
  Nil
}

fn load_series_for_study(study_uid: String) -> Effect(Msg) {
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
    dispatch(SeriesLoaded(series_result))
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
