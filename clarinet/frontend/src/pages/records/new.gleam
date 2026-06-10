// New record creation page — self-contained MVU module.
//
// Hosts in two modes:
//   * `FullPage` — `/records/new` standalone page (existing behaviour).
//   * `Modal(args)` — embedded as a modal overlay on Patient/Study/Series
//     detail pages with `args` carrying the prefilled context. The page
//     model, update, and form view are reused; only `init`/submit-success/
//     cancel branches differ between modes.
import api/models.{type Record, type RecordType, type Series, type Study}
import api/patients
import api/records
import api/studies
import api/types.{type ApiError, AuthError}
import cache/bucket
import components/forms/record_form
import gleam/bool
import gleam/dict.{type Dict}
import gleam/int
import gleam/javascript/promise
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import router
import shared.{type OutMsg, type Shared}
import utils/permissions

// --- Model ---

/// How this page is being hosted. Affects init prefill, view chrome, and
/// the post-submit OutMsg sequence.
pub type HostMode {
  FullPage
  Modal(args: shared.OpenCreateRecordModalArgs)
}

pub type Model {
  Model(
    host_mode: HostMode,
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
      host_mode: FullPage,
      form_data: record_form.init(),
      form_errors: dict.new(),
      form_studies: [],
      form_series: [],
      loading: False,
      studies_request_id: 0,
      series_request_id: 0,
    )
  // ReloadPatients is required even for non-admins — the form needs the
  // patient picker. The backend is expected to scope `/api/patients` results
  // by the caller's permissions.
  let out_msgs = case shared.user {
    Some(u) ->
      case permissions.is_admin_user(u) {
        True -> [
          shared.ReloadPatients,
          shared.ReloadRecordTypes,
          shared.ReloadUsers,
        ]
        False -> [shared.ReloadPatients, shared.ReloadRecordTypes]
      }
    None -> [shared.ReloadPatients, shared.ReloadRecordTypes]
  }
  #(model, effect.none(), out_msgs)
}

/// Initialize this page as the contents of the modal create-record overlay.
/// Prefills the form with the locked context fields, eagerly loads the studies
/// list for the patient (so the user can pick a Study/Series RecordType without
/// extra clicks), and loads the series list when the source page already
/// pinned a Study UID.
///
/// **Page Module Contract exception:** `.claude/rules/frontend-page-contract.md` §1 lists
/// only `init` / `update` / `view` / `cleanup` as public symbols of a page
/// module. `init_modal` is an explicit exception — modal hosting requires a
/// separate prefilled init path that `main.init_page_for_route` cannot
/// reach (modals are opened by `OutMsg`, not by route). This is the only
/// page in the codebase with this dual-init pattern; new modal-hosted
/// pages should follow the same convention rather than inventing a new one.
pub fn init_modal(
  args: shared.OpenCreateRecordModalArgs,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  // Project each args variant onto the flat prefill tuple consumed by
  // RecordFormData. Adding a new variant only touches this match.
  let #(
    patient_id,
    study_uid_str,
    series_uid_str,
    parent_record_id_str,
    context_info_str,
  ) = case args {
    shared.PatientArgs(pid) -> #(pid, "", "", "", "")
    shared.StudyArgs(pid, suid) -> #(pid, suid, "", "", "")
    shared.SeriesArgs(pid, suid, seruid) -> #(pid, suid, seruid, "", "")
    shared.RecordArgs(pid, suid, seruid, parent_id, prefill) -> #(
      pid,
      option.unwrap(suid, ""),
      option.unwrap(seruid, ""),
      int.to_string(parent_id),
      prefill,
    )
  }
  let form_data =
    record_form.RecordFormData(
      record_type_name: "",
      patient_id: patient_id,
      study_uid: study_uid_str,
      series_uid: series_uid_str,
      user_id: "",
      parent_record_id: parent_record_id_str,
      context_info: context_info_str,
    )
  // Studies list is only needed when the user can actually pick a study
  // (i.e. the source page didn't already pin one). Otherwise the studies
  // dropdown is replaced by a locked input, and an HTTP fetch would be wasted.
  // For RecordArgs we eagerly load when no study_uid is pinned.
  let studies_eff = case args {
    shared.PatientArgs(pid) -> load_studies_for_patient(1, pid)
    shared.RecordArgs(pid, None, _, _, _) -> load_studies_for_patient(1, pid)
    shared.StudyArgs(_, _)
    | shared.SeriesArgs(_, _, _)
    | shared.RecordArgs(_, Some(_), _, _, _) -> effect.none()
  }
  // Series list is only needed when a study is pinned but no series — i.e.
  // the user is on a Study page picking a series-level RecordType.
  let series_eff = case args {
    shared.StudyArgs(_, suid) -> load_series_for_study(1, suid)
    shared.RecordArgs(_, Some(suid), None, _, _) ->
      load_series_for_study(1, suid)
    shared.PatientArgs(_)
    | shared.SeriesArgs(_, _, _)
    | shared.RecordArgs(_, None, _, _, _)
    | shared.RecordArgs(_, _, Some(_), _, _) -> effect.none()
  }
  let model =
    Model(
      host_mode: Modal(args),
      form_data: form_data,
      form_errors: dict.new(),
      form_studies: [],
      form_series: [],
      loading: False,
      studies_request_id: 1,
      series_request_id: 1,
    )
  // The full-page form's `init` also reloads patients and (for admins) users;
  // the modal hides those pickers entirely (`hidden_fields` in the view), so
  // we skip the corresponding HTTP. RecordTypes remain mandatory.
  #(model, effect.batch([studies_eff, series_eff]), [shared.ReloadRecordTypes])
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
      let raw_new_data = record_form.update(old_data, form_msg)
      // In modal mode `record_form.update` would clear the prefilled,
      // locked context fields on `UpdateRecordType` (which always resets
      // study_uid / series_uid). Restore them from `args` so a type swap
      // doesn't break a form whose disabled inputs still display the value.
      let new_data = case model.host_mode {
        Modal(shared.PatientArgs(pid)) ->
          record_form.RecordFormData(..raw_new_data, patient_id: pid)
        Modal(shared.StudyArgs(pid, suid)) ->
          record_form.RecordFormData(
            ..raw_new_data,
            patient_id: pid,
            study_uid: suid,
          )
        Modal(shared.SeriesArgs(pid, suid, seruid)) ->
          record_form.RecordFormData(
            ..raw_new_data,
            patient_id: pid,
            study_uid: suid,
            series_uid: seruid,
          )
        // RecordArgs: study/series UIDs may be unset on the source Record;
        // only restore them when present in args, otherwise leave whatever
        // the user just selected so the cascading pickers keep working.
        Modal(shared.RecordArgs(pid, suid_opt, seruid_opt, parent_id, _)) ->
          record_form.RecordFormData(
            ..raw_new_data,
            patient_id: pid,
            study_uid: option.unwrap(suid_opt, raw_new_data.study_uid),
            series_uid: option.unwrap(seruid_opt, raw_new_data.series_uid),
            parent_record_id: int.to_string(parent_id),
          )
        FullPage -> raw_new_data
      }
      let updated_model = Model(..model, form_data: new_data)

      // Cascade: patient changed → load studies
      let patient_changed = old_data.patient_id != new_data.patient_id
      let study_changed = old_data.study_uid != new_data.study_uid
      let type_changed = old_data.record_type_name != new_data.record_type_name

      // Reset studies/series on patient or type change
      let updated_model = case patient_changed || type_changed {
        True -> Model(..updated_model, form_studies: [], form_series: [])
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

      let #(updated_model, studies_eff) = {
        use <- bool.guard(!needs_studies_load, #(updated_model, effect.none()))
        let new_id = updated_model.studies_request_id + 1
        let m = Model(..updated_model, studies_request_id: new_id)
        #(m, load_studies_for_patient(new_id, new_data.patient_id))
      }

      // Load series when study selected/changed
      let needs_series_load = study_changed && new_data.study_uid != ""
      let #(updated_model, series_eff) = {
        use <- bool.guard(!needs_series_load, #(updated_model, effect.none()))
        let new_id = updated_model.series_request_id + 1
        let m = Model(..updated_model, series_request_id: new_id)
        #(m, load_series_for_study(new_id, new_data.study_uid))
      }

      #(updated_model, effect.batch([studies_eff, series_eff]), [])
    }

    StudiesLoaded(request_id, Ok(studies_list)) -> {
      use <- bool.guard(
        request_id != model.studies_request_id,
        #(model, effect.none(), []),
      )
      #(Model(..model, form_studies: studies_list), effect.none(), [])
    }

    StudiesLoaded(request_id, Error(_)) -> {
      use <- bool.guard(
        request_id != model.studies_request_id,
        #(model, effect.none(), []),
      )
      #(Model(..model, form_studies: []), effect.none(), [])
    }

    SeriesLoaded(request_id, Ok(series_list)) -> {
      use <- bool.guard(
        request_id != model.series_request_id,
        #(model, effect.none(), []),
      )
      #(Model(..model, form_series: series_list), effect.none(), [])
    }

    SeriesLoaded(request_id, Error(_)) -> {
      use <- bool.guard(
        request_id != model.series_request_id,
        #(model, effect.none(), []),
      )
      #(Model(..model, form_series: []), effect.none(), [])
    }

    Submit -> {
      let base_result =
        record_form.validate(model.form_data, shared.cache.record_types)
      let parent_required =
        record_type_requires_parent(
          model.form_data.record_type_name,
          shared.cache.record_types,
        )
      // Empty input and non-integer input both produce ``None`` on submit
      // (see the ``int.parse`` branch below); both must trigger the guard.
      let parent_id_valid = case model.form_data.parent_record_id {
        "" -> False
        v ->
          case int.parse(v) {
            Ok(_) -> True
            Error(_) -> False
          }
      }
      let parent_missing = parent_required && !parent_id_valid
      let parent_error = #("parent_record_id", "Parent record is required")
      // Merge the parent-required guard into whatever record_form.validate
      // returned so the form surfaces both classes of errors in one pass.
      let result = case base_result, parent_missing {
        _, False -> base_result
        Ok(_), True -> Error(dict.from_list([parent_error]))
        Error(errs), True ->
          Error(dict.insert(errs, parent_error.0, parent_error.1))
      }
      case result {
        Ok(_) -> {
          let data = model.form_data
          let record_create =
            models.RecordCreate(
              record_type_name: data.record_type_name,
              patient_id: data.patient_id,
              status: types.Pending,
              study_uid: optional_string(data.study_uid),
              series_uid: optional_string(data.series_uid),
              user_id: optional_string(data.user_id),
              parent_record_id: case data.parent_record_id {
                "" -> None
                v ->
                  case int.parse(v) {
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
          #(Model(..model, loading: True, form_errors: dict.new()), eff, [])
        }
        Error(errors) -> #(
          Model(..model, form_errors: errors),
          effect.none(),
          [],
        )
      }
    }

    SubmitResult(Ok(record)) -> {
      case model.host_mode {
        FullPage -> {
          let route = case record.id {
            Some(id) -> router.RecordDetail(int.to_string(id))
            None -> router.Records(dict.new())
          }
          #(Model(..model, loading: False), effect.none(), [
            shared.CacheRecord(record),
            shared.ShowSuccess("Record created successfully"),
            shared.Navigate(route),
          ])
        }
        Modal(args) -> {
          // Stay on the source page, refresh the records section for that
          // page's context (bucket for Patient/Study, get_series re-fetch
          // for Series since its records are nested in the Series object).
          let invalidate = case args {
            // The patient detail records list is now a sortable/filterable
            // bucket, so its exact key (which depends on the page's active
            // sort/filter) isn't known here. Invalidate all record buckets —
            // the new record also belongs in the global /records list anyway.
            shared.PatientArgs(_) -> shared.InvalidateAllRecordBuckets
            shared.StudyArgs(_, suid) ->
              shared.InvalidateBucket(
                bucket.Records(bucket.query_with_study(suid)),
              )
            shared.SeriesArgs(_, _, seruid) -> shared.ReloadSeries(seruid)
            // Source-Record-keyed bucket doesn't exist (children-of-record
            // isn't a server filter); invalidate all record buckets so any
            // visible list refetches and shows the new child.
            shared.RecordArgs(_, _, _, _, _) ->
              shared.InvalidateAllRecordBuckets
          }
          #(Model(..model, loading: False), effect.none(), [
            shared.CacheRecord(record),
            shared.ShowSuccess("Record created successfully"),
            invalidate,
            shared.CloseRecordModal,
          ])
        }
      }
    }

    SubmitResult(Error(err)) -> #(
      Model(..model, loading: False),
      effect.none(),
      handle_error(err, "Failed to create record"),
    )

    Cancel ->
      case model.host_mode {
        FullPage -> #(model, effect.none(), [
          shared.Navigate(router.Records(dict.new())),
        ])
        Modal(_) -> #(model, effect.none(), [shared.CloseRecordModal])
      }
  }
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
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
  let locked = compute_locked_fields(model.host_mode)
  let parent_required =
    record_type_requires_parent(
      model.form_data.record_type_name,
      shared.cache.record_types,
    )
  let hidden = compute_hidden_fields(model.host_mode, parent_required)
  let expected_level = expected_level_for(model.host_mode)
  let form =
    record_form.view(
      data: model.form_data,
      studies: model.form_studies,
      series_list: model.form_series,
      errors: model.form_errors,
      loading: model.loading,
      locked_fields: locked,
      hidden_fields: hidden,
      expected_level: expected_level,
      shared: shared,
      on_update: fn(msg) { UpdateForm(msg) },
      on_submit: fn() { Submit },
      on_cancel: Cancel,
    )
  case model.host_mode {
    FullPage ->
      html.div([attribute.class("container")], [
        html.h1([], [html.text("New Record")]),
        form,
      ])
    Modal(shared.RecordArgs(_, _, _, parent_id, _)) ->
      html.div([], [render_parent_pill(parent_id), form])
    Modal(_) -> form
  }
}

// Read-only header pill surfacing the source Record id in the create-from-
// Record flow. The parent_record_id is also written to form_data but kept
// hidden (see compute_hidden_fields) — this pill is the only visible cue
// that the new Record will inherit a parent.
fn render_parent_pill(parent_id: Int) -> Element(Msg) {
  html.div([attribute.class("create-from-record-pill")], [
    html.text("Created from Record #" <> int.to_string(parent_id)),
  ])
}

/// Compute which form fields render as read-only inputs.
/// Public for unit testing — the body is a pure function of `host_mode`.
///
/// For `RecordArgs` we only lock the UIDs that the source Record actually
/// has — when `study_uid` / `series_uid` are `None`, the cascading picker
/// stays interactive so the user can complete the missing context.
pub fn compute_locked_fields(host_mode: HostMode) -> List(String) {
  case host_mode {
    FullPage -> []
    Modal(shared.PatientArgs(_)) -> ["patient_id"]
    Modal(shared.StudyArgs(_, _)) -> ["study_uid", "patient_id"]
    Modal(shared.SeriesArgs(_, _, _)) -> [
      "series_uid",
      "study_uid",
      "patient_id",
    ]
    Modal(shared.RecordArgs(_, study_uid_opt, series_uid_opt, _, _)) -> {
      let base = ["patient_id"]
      let with_study = case study_uid_opt {
        Some(_) -> ["study_uid", ..base]
        None -> base
      }
      case series_uid_opt {
        Some(_) -> ["series_uid", ..with_study]
        None -> with_study
      }
    }
  }
}

/// Compute which form fields are entirely omitted from the rendered form.
/// Public for unit testing — the body is a pure function of `host_mode`
/// and the selected record_type's ``parent_required`` flag.
///
/// `user_id` is hidden in every modal mode. `parent_record_id` is hidden
/// for the three context-only variants when the selected RecordType does
/// not require a parent. When ``parent_required`` is set, the picker is
/// revealed so the user can select one. Under `RecordArgs` the parent is
/// always preset from args (surfaced via the read-only header pill), so
/// the picker stays hidden regardless of ``parent_required``.
pub fn compute_hidden_fields(
  host_mode: HostMode,
  parent_required: Bool,
) -> List(String) {
  case host_mode {
    FullPage -> []
    Modal(shared.RecordArgs(_, _, _, _, _)) -> ["user_id", "parent_record_id"]
    Modal(_) ->
      case parent_required {
        True -> ["user_id"]
        False -> ["user_id", "parent_record_id"]
      }
  }
}

/// Lookup whether the selected record_type has ``parent_required=True``.
/// ``""`` (no type selected) and unknown names map to ``False`` so the
/// hidden-fields / submit-validation paths default to the existing behaviour.
pub fn record_type_requires_parent(
  record_type_name: String,
  record_types: Dict(String, RecordType),
) -> Bool {
  case dict.get(record_types, record_type_name) {
    Ok(rt) -> rt.parent_required
    Error(_) -> False
  }
}

/// Derive the level the modal is "locked to" from the args variant. Used
/// by `view` to disable record_type options whose level wouldn't match
/// the locked UID context (else the backend `validate_record_level`
/// rejects the create with 422).
///
/// For `RecordArgs` the level follows the deepest filled UID — same rule
/// the backend uses to decide whether a payload is valid.
pub fn expected_level_for(host_mode: HostMode) -> Option(types.DicomQueryLevel) {
  case host_mode {
    FullPage -> None
    Modal(shared.PatientArgs(_)) -> Some(types.Patient)
    Modal(shared.StudyArgs(_, _)) -> Some(types.Study)
    Modal(shared.SeriesArgs(_, _, _)) -> Some(types.Series)
    Modal(shared.RecordArgs(_, study_uid_opt, series_uid_opt, _, _)) ->
      case series_uid_opt, study_uid_opt {
        Some(_), _ -> Some(types.Series)
        None, Some(_) -> Some(types.Study)
        None, None -> Some(types.Patient)
      }
  }
}
