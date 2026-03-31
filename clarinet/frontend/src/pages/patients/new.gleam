// New patient creation page — self-contained MVU module
import api/models.{type Patient}
import api/patients
import api/types.{type ApiError, AuthError}
import components/forms/patient_form
import gleam/dict.{type Dict}
import gleam/javascript/promise
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import router
import shared.{type OutMsg, type Shared}

// --- Model ---

pub type Model {
  Model(
    form_id: String,
    form_name: String,
    form_errors: Dict(String, String),
    loading: Bool,
  )
}

// --- Msg ---

pub type Msg {
  UpdateForm(patient_form.PatientFormMsg)
  Submit
  SubmitResult(Result(Patient, ApiError))
  Cancel
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg)) {
  let model =
    Model(
      form_id: "",
      form_name: "",
      form_errors: dict.new(),
      loading: False,
    )
  #(model, effect.none())
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    UpdateForm(form_msg) -> {
      let data =
        patient_form.PatientFormData(id: model.form_id, name: model.form_name)
      let updated = patient_form.update(data, form_msg)
      #(
        Model(..model, form_id: updated.id, form_name: updated.name),
        effect.none(),
        [],
      )
    }

    Submit -> {
      let form_data =
        patient_form.PatientFormData(id: model.form_id, name: model.form_name)
      case patient_form.validate(form_data) {
        Ok(data) -> {
          let eff = {
            use dispatch <- effect.from
            patients.create_patient(data.id, data.name)
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

    SubmitResult(Ok(patient)) ->
      #(Model(..model, loading: False), effect.none(), [
        shared.CachePatient(patient),
        shared.ShowSuccess("Patient created successfully"),
        shared.Navigate(router.PatientDetail(patient.id)),
      ])

    SubmitResult(Error(err)) ->
      #(Model(..model, loading: False), effect.none(), handle_error(err, "Failed to create patient"))

    Cancel ->
      #(model, effect.none(), [shared.Navigate(router.Patients)])
  }
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.ShowError(fallback_msg)]
  }
}

// --- View ---

pub fn view(model: Model, _shared: Shared) -> Element(Msg) {
  let form_data =
    patient_form.PatientFormData(id: model.form_id, name: model.form_name)

  html.div([attribute.class("container")], [
    html.h1([], [html.text("New Patient")]),
    patient_form.view(
      data: form_data,
      errors: model.form_errors,
      loading: model.loading,
      on_update: fn(msg) { UpdateForm(msg) },
      on_submit: fn() { Submit },
      on_cancel: Cancel,
    ),
  ])
}
