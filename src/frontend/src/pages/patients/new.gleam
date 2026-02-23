// New patient creation page (admin only)
import components/forms/patient_form
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import store.{type Model, type Msg}

pub fn view(model: Model) -> Element(Msg) {
  let form_data =
    patient_form.PatientFormData(
      id: model.patient_form_id,
      name: model.patient_form_name,
    )

  html.div([attribute.class("container")], [
    html.h1([], [html.text("New Patient")]),
    patient_form.view(
      data: form_data,
      errors: model.form_errors,
      loading: model.loading,
      on_update: fn(msg) {
        case msg {
          patient_form.UpdatePatientId(v) -> store.UpdatePatientFormId(v)
          patient_form.UpdatePatientName(v) -> store.UpdatePatientFormName(v)
        }
      },
      on_submit: fn() { store.SubmitPatientForm },
    ),
  ])
}
