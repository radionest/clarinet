// Main Lustre application
import api/admin
import api/auth
import api/dicom
import api/models
import api/patients
import api/records
import api/series
import api/slicer
import api/studies
import api/types
import api/users
import components/forms/patient_form
import components/layout
import formosh/component as formosh_component
import gleam/dict
import gleam/int
import gleam/io
import gleam/javascript/promise
import gleam/string
import gleam/list
import gleam/option.{None, Some}
import gleam/uri.{type Uri}
import lustre
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import modem
import pages/admin as admin_page
import pages/record_types/detail as record_type_detail
import pages/record_types/edit as record_type_edit
import pages/record_types/list as record_types_list
import pages/home
import pages/login
import pages/patients/detail as patient_detail
import pages/patients/list as patients_list
import pages/patients/new as patient_new
import pages/records/execute as record_execute
import pages/records/list as records_list
import pages/register
import pages/series/detail as series_detail
import pages/studies/detail as study_detail
import pages/studies/list as studies_list
import router.{type Route}
import store.{type Model, type Msg}

// Initialize the application
pub fn main() {
  let app = lustre.application(init, update, view)
  let assert Ok(_) = lustre.start(app, "#app", Nil)
  Nil
}

// Initialize with routing
fn init(_flags) -> #(Model, Effect(Msg)) {
  let model = store.init()

  // Set up routing with modem
  let initial_route = case modem.initial_uri() {
    Ok(uri) -> router.parse_route(uri)
    Error(_) -> router.Home
  }

  let model_with_route = store.set_route(model, initial_route)

  // Register formosh web component
  let register_formosh_effect =
    effect.from(fn(_dispatch) {
      let _ = formosh_component.register()
      Nil
    })

  // Check existing session via cookie
  let check_session_effect = {
    use dispatch <- effect.from
    auth.get_current_user()
    |> promise.tap(fn(result) { dispatch(store.CheckSessionResult(result)) })
    Nil
  }

  #(
    model_with_route,
    effect.batch([
      modem.init(on_url_change),
      register_formosh_effect,
      check_session_effect,
    ]),
  )
}

// Handle URL changes from modem
fn on_url_change(uri: Uri) -> Msg {
  io.println(">>> on_url_change fired, uri: " <> string.inspect(uri))
  let route = router.parse_route(uri)
  io.println(">>> parsed route: " <> string.inspect(route))
  store.OnRouteChange(route)
}

// Update function
pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  case msg {
    // Routing
    store.OnRouteChange(route) -> {
      io.println(
        ">>> OnRouteChange handler, route: " <> string.inspect(route)
        <> ", checking_session: " <> string.inspect(model.checking_session)
        <> ", user: " <> string.inspect(model.user),
      )
      let new_model = store.set_route(model, route)

      // Don't redirect while session check is in progress
      case model.checking_session {
        True -> #(new_model, effect.none())
        False -> {
          // Check authentication requirement
          let is_auth_page = route == router.Login || route == router.Register
          case router.requires_auth(route), model.user, is_auth_page {
            True, None, _ -> {
              // Redirect to login if auth required
              #(
                store.set_route(model, router.Login),
                modem.push("/login", option.None, option.None),
              )
            }
            False, Some(_), True -> {
              // Redirect from login/register if already authenticated
              #(
                store.set_route(model, router.Home),
                modem.push("/", option.None, option.None),
              )
            }
            _, _, _ ->
              case router.requires_admin_role(route), model.user {
                True, Some(models.User(is_superuser: False, ..)) -> #(
                  store.set_route(model, router.Home),
                  modem.push("/", option.None, option.None),
                )
                _, _ -> #(new_model, load_route_data(new_model, route))
              }
          }
        }
      }
    }

    store.Navigate(route) -> {
      io.println(">>> Navigate handler, route: " <> string.inspect(route))
      // Use Modem to update URL without page reload
      let path = router.route_to_path(route)
      io.println(">>> Navigate pushing path: " <> path)
      #(model, modem.push(path, option.None, option.None))
    }

    // Session restoration
    store.CheckSessionResult(result) -> {
      case result {
        Ok(user) -> {
          // Session is valid - restore user and load route data
          let new_model =
            model
            |> store.set_user(user)
            |> fn(m) { store.Model(..m, checking_session: False) }
          let route = model.route
          let is_auth_page = route == router.Login || route == router.Register
          case router.requires_auth(route), new_model.user, is_auth_page {
            False, Some(_), True ->
              #(
                store.set_route(new_model, router.Home),
                modem.push("/", option.None, option.None),
              )
            _, _, _ ->
              case router.requires_admin_role(route), new_model.user {
                True, Some(models.User(is_superuser: False, ..)) -> #(
                  store.set_route(new_model, router.Home),
                  modem.push("/", option.None, option.None),
                )
                _, _ -> #(new_model, load_route_data(new_model, route))
              }
          }
        }
        Error(_) -> {
          // No valid session - redirect to login if on protected route
          let new_model = store.Model(..model, checking_session: False)
          case router.requires_auth(model.route) {
            True ->
              #(
                store.set_route(new_model, router.Login),
                modem.push("/login", option.None, option.None),
              )
            False -> #(new_model, effect.none())
          }
        }
      }
    }

    // Auth form updates
    store.LoginUpdateEmail(value) -> {
      #(store.Model(..model, login_email: value), effect.none())
    }

    store.LoginUpdatePassword(value) -> {
      #(store.Model(..model, login_password: value), effect.none())
    }

    store.RegisterUpdateEmail(value) -> {
      #(store.Model(..model, register_email: value), effect.none())
    }

    store.RegisterUpdatePassword(value) -> {
      #(store.Model(..model, register_password: value), effect.none())
    }

    store.RegisterUpdatePasswordConfirm(value) -> {
      #(store.Model(..model, register_password_confirm: value), effect.none())
    }

    // Authentication
    store.LoginSubmit(email, password) -> {
      let login_effect = {
        use dispatch <- effect.from
        auth.login(email, password)
        |> promise.tap(fn(result) {
          case result {
            Ok(response) -> dispatch(store.LoginSuccess(response.user))
            Error(error) -> dispatch(store.LoginError(error))
          }
        })
        Nil
      }
      #(store.set_loading(model, True), login_effect)
    }

    store.LoginSuccess(user) -> {
      // Cookie authentication is handled automatically
      // Just update the model with the user
      let new_model =
        model
        |> store.set_user(user)
        |> store.set_loading(False)
        |> store.clear_messages()
        |> store.clear_auth_forms()
        |> store.set_route(router.Home)

      #(new_model, modem.push("/", option.None, option.None))
    }

    store.LoginError(error) -> {
      let error_msg = case error {
        types.AuthError(msg) -> msg
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Login failed. Please try again."
      }

      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some(error_msg))

      #(new_model, effect.none())
    }

    store.RegisterSubmit(email, password) -> {
      let register_request =
        models.RegisterRequest(email: email, password: password)
      let register_effect = {
        use dispatch <- effect.from
        auth.register(register_request)
        |> promise.tap(fn(result) {
          case result {
            Ok(user) -> dispatch(store.RegisterSuccess(user))
            Error(error) -> dispatch(store.RegisterError(error))
          }
        })
        Nil
      }
      #(
        store.set_loading(model, True) |> store.clear_messages(),
        register_effect,
      )
    }

    store.RegisterSuccess(user) -> {
      // Registration successful - user is logged in via cookie
      let new_model =
        model
        |> store.set_user(user)
        |> store.set_loading(False)
        |> store.clear_messages()
        |> store.clear_auth_forms()
        |> store.set_success("Registration successful! Welcome to Clarinet.")
        |> store.set_route(router.Home)

      #(new_model, modem.push("/", option.None, option.None))
    }

    store.RegisterError(error) -> {
      let error_msg = case error {
        types.ValidationError(_) ->
          "Invalid registration data. Please check your inputs."
        types.AuthError(msg) -> msg
        types.ServerError(409, _) -> "Username or email already exists."
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Registration failed. Please try again."
      }

      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some(error_msg))

      #(new_model, effect.none())
    }

    store.Logout -> {
      let logout_effect = {
        use dispatch <- effect.from
        auth.logout()
        |> promise.tap(fn(_) { dispatch(store.LogoutComplete) })
        Nil
      }
      #(store.clear_user(model), logout_effect)
    }

    store.LogoutComplete -> {
      #(
        store.set_route(model, router.Login),
        modem.push("/login", option.None, option.None),
      )
    }

    // UI Messages
    store.ClearError -> {
      #(store.clear_messages(model), effect.none())
    }

    store.ClearSuccessMessage -> {
      #(store.clear_messages(model), effect.none())
    }

    // Data loading - Studies
    store.LoadStudies ->
      load_with_effect(model, studies.get_studies, store.StudiesLoaded)

    store.StudiesLoaded(Ok(studies_list)) -> {
      let studies_dict =
        list.fold(studies_list, model.studies, fn(acc, study) {
          dict.insert(acc, study.study_uid, study)
        })
      let new_model =
        store.Model(..model, studies: studies_dict, loading: False)
      #(new_model, effect.none())
    }

    store.StudiesLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load studies")

    // Data loading - Records (role-aware)
    store.LoadRecords -> {
      let fetch_fn = case model.user {
        Some(models.User(is_superuser: True, ..)) -> records.get_records
        _ -> records.get_my_records
      }
      load_with_effect(model, fetch_fn, store.RecordsLoaded)
    }

    store.RecordsLoaded(Ok(records_list)) -> {
      let records_dict =
        list.fold(records_list, model.records, fn(acc, record) {
          case record.id {
            Some(id) -> dict.insert(acc, int.to_string(id), record)
            None -> acc
          }
        })
      let new_model =
        store.Model(..model, records: records_dict, loading: False)
      #(new_model, effect.none())
    }

    store.RecordsLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load records")

    // Data loading - Users
    store.LoadUsers ->
      load_with_effect(model, users.get_users, store.UsersLoaded)

    store.UsersLoaded(Ok(users_list)) -> {
      let users_dict =
        list.fold(users_list, model.users, fn(acc, user) {
          dict.insert(acc, user.id, user)
        })
      let new_model =
        store.Model(..model, users: users_dict, loading: False)
      #(new_model, effect.none())
    }

    store.UsersLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load users")

    // Data loading - Admin Stats
    store.LoadAdminStats ->
      load_with_effect(model, admin.get_admin_stats, store.AdminStatsLoaded)

    store.AdminStatsLoaded(Ok(stats)) -> {
      let new_model =
        store.Model(..model, admin_stats: Some(stats), loading: False)
      #(new_model, effect.none())
    }

    store.AdminStatsLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load admin statistics")

    // Data loading - Record Type Stats
    store.LoadRecordTypeStats ->
      load_with_effect(
        model,
        admin.get_record_type_stats,
        store.RecordTypeStatsLoaded,
      )

    store.RecordTypeStatsLoaded(Ok(stats)) -> {
      let new_model =
        store.Model(..model, record_type_stats: Some(stats), loading: False)
      #(new_model, effect.none())
    }

    store.RecordTypeStatsLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load record type statistics")

    // Admin record assignment
    store.AdminToggleAssignDropdown(record_id) -> {
      #(store.Model(..model, admin_editing_record_id: record_id), effect.none())
    }

    store.AdminAssignUser(record_id, user_id) -> {
      let assign_effect = {
        use dispatch <- effect.from
        admin.assign_record_user(record_id, user_id)
        |> promise.tap(fn(result) {
          dispatch(store.AdminAssignUserResult(result))
        })
        Nil
      }
      #(
        store.Model(..model, admin_editing_record_id: None, loading: True),
        assign_effect,
      )
    }

    store.AdminAssignUserResult(Ok(record)) -> {
      let new_model =
        model
        |> store.update_record(record)
        |> store.set_loading(False)
        |> store.set_success("User assigned successfully")
      #(new_model, effect.none())
    }

    store.AdminAssignUserResult(Error(err)) ->
      handle_api_error(model, err, "Failed to assign user to record")

    // Data loading - Record Detail
    store.LoadRecordDetail(id) ->
      load_with_effect(
        model,
        fn() { records.get_record(id) },
        store.RecordDetailLoaded,
      )

    store.RecordDetailLoaded(Ok(record)) -> {
      let new_model =
        model
        |> store.cache_record(record)
        |> store.set_loading(False)
      #(new_model, effect.none())
    }

    store.RecordDetailLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load record")

    // Formosh form events
    store.FormSubmitSuccess(record_id) -> {
      // Check if this record type has a validator or slicer_script
      let slicer_effect = case dict.get(model.records, record_id) {
        Ok(models.Record(record_type: Some(models.RecordType(slicer_result_validator: Some(_), ..)), ..)) ->
          // Has validator: validate first, scene will clear after validation succeeds
          dispatch_msg(store.SlicerValidate(record_id))
        Ok(models.Record(record_type: Some(models.RecordType(slicer_script: Some(_), ..)), ..)) ->
          // Has slicer_script but no validator: clear scene directly
          dispatch_msg(store.SlicerClearScene)
        _ -> effect.none()
      }
      #(
        store.set_success(model, "Record data submitted successfully"),
        effect.batch([
          dispatch_msg(store.LoadRecordDetail(record_id)),
          slicer_effect,
        ]),
      )
    }

    store.FormSubmitError(error) -> {
      #(store.set_error(model, Some(error)), effect.none())
    }

    // RecordType edit
    store.LoadRecordTypeForEdit(name) ->
      load_with_effect(
        model,
        fn() { records.get_record_type(name) },
        store.RecordTypeForEditLoaded,
      )

    store.RecordTypeForEditLoaded(Ok(rt)) -> {
      let new_model =
        model
        |> store.cache_record_type(rt)
        |> store.set_loading(False)
      #(new_model, effect.none())
    }

    store.RecordTypeForEditLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load record type"))
      #(new_model, effect.none())
    }

    store.RecordTypeEditSuccess(name) -> {
      let new_model =
        model
        |> store.set_success("Record type updated successfully")
        |> store.set_route(router.AdminRecordTypeDetail(name))
      #(
        new_model,
        effect.batch([
          modem.push(
            router.route_to_path(router.AdminRecordTypeDetail(name)),
            option.None,
            option.None,
          ),
          dispatch_msg(store.LoadRecordTypeStats),
        ]),
      )
    }

    store.RecordTypeEditError(error) -> {
      #(store.set_error(model, Some(error)), effect.none())
    }

    // Slicer operations
    store.OpenInSlicer(record_id) -> {
      let open_effect = {
        use dispatch <- effect.from
        slicer.open_record(record_id)
        |> promise.tap(fn(result) { dispatch(store.SlicerOpenResult(result)) })
        Nil
      }
      #(store.Model(..model, slicer_loading: True), open_effect)
    }

    store.SlicerOpenResult(Ok(_)) -> {
      #(
        store.Model(..model, slicer_loading: False)
          |> store.set_success("Workspace opened in 3D Slicer"),
        effect.none(),
      )
    }

    store.SlicerOpenResult(Error(err)) -> {
      let error_msg = case err {
        types.ServerError(502, _) -> "3D Slicer is not reachable. Is it running?"
        types.ServerError(_, msg) -> "Slicer error: " <> msg
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Failed to open record in Slicer"
      }
      #(
        store.Model(..model, slicer_loading: False)
          |> store.set_error(Some(error_msg)),
        effect.none(),
      )
    }

    store.SlicerValidate(record_id) -> {
      let validate_effect = {
        use dispatch <- effect.from
        slicer.validate_record(record_id)
        |> promise.tap(fn(result) {
          dispatch(store.SlicerValidateResult(result))
        })
        Nil
      }
      #(store.Model(..model, slicer_loading: True), validate_effect)
    }

    store.SlicerValidateResult(Ok(_)) -> {
      #(
        store.Model(..model, slicer_loading: False)
          |> store.set_success("Slicer validation completed"),
        dispatch_msg(store.SlicerClearScene),
      )
    }

    store.SlicerValidateResult(Error(err)) -> {
      let error_msg = case err {
        types.ServerError(502, _) ->
          "3D Slicer is not reachable for validation. Is it running?"
        types.ServerError(_, msg) -> "Validation error: " <> msg
        types.NetworkError(msg) -> "Network error: " <> msg
        _ -> "Slicer validation failed"
      }
      #(
        store.Model(..model, slicer_loading: False)
          |> store.set_error(Some(error_msg)),
        effect.none(),
      )
    }

    store.SlicerClearScene -> {
      let clear_effect = {
        use dispatch <- effect.from
        slicer.clear_scene()
        |> promise.tap(fn(result) {
          dispatch(store.SlicerClearSceneResult(result))
        })
        Nil
      }
      #(model, clear_effect)
    }

    store.SlicerClearSceneResult(_) -> {
      // Silently ignore both success and error — data is already saved
      #(model, effect.none())
    }

    store.SlicerPing -> {
      let ping_effect = {
        use dispatch <- effect.from
        slicer.ping()
        |> promise.tap(fn(result) { dispatch(store.SlicerPingResult(result)) })
        Nil
      }
      #(model, ping_effect)
    }

    store.SlicerPingResult(Ok(_)) -> {
      #(store.Model(..model, slicer_available: Some(True)), effect.none())
    }

    store.SlicerPingResult(Error(_)) -> {
      #(store.Model(..model, slicer_available: Some(False)), effect.none())
    }

    // Filters
    store.AddFilter(key, value) -> {
      #(store.apply_filter(model, key, value), effect.none())
    }
    store.RemoveFilter(key) -> {
      #(store.remove_filter(model, key), effect.none())
    }
    store.ClearFilters -> {
      #(store.clear_filters(model), effect.none())
    }

    // Patient data loading
    store.LoadPatients ->
      load_with_effect(model, patients.get_patients, store.PatientsLoaded)

    store.PatientsLoaded(Ok(patients_list)) -> {
      let patients_dict =
        list.fold(patients_list, model.patients, fn(acc, patient) {
          dict.insert(acc, patient.id, patient)
        })
      let new_model =
        store.Model(..model, patients: patients_dict, loading: False)
      #(new_model, effect.none())
    }

    store.PatientsLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load patients")

    store.LoadPatientDetail(id) ->
      load_with_effect(
        model,
        fn() { patients.get_patient(id) },
        store.PatientDetailLoaded,
      )

    store.PatientDetailLoaded(Ok(patient)) -> {
      let new_model =
        model
        |> store.cache_patient(patient)
        |> store.set_loading(False)
      #(new_model, effect.none())
    }

    store.PatientDetailLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load patient")

    // Patient form handling
    store.UpdatePatientFormId(value) -> {
      #(store.Model(..model, patient_form_id: value), effect.none())
    }

    store.UpdatePatientFormName(value) -> {
      #(store.Model(..model, patient_form_name: value), effect.none())
    }

    store.SubmitPatientForm -> {
      let form_data =
        patient_form.PatientFormData(
          id: model.patient_form_id,
          name: model.patient_form_name,
        )
      case patient_form.validate(form_data) {
        Ok(data) -> {
          let submit_effect = {
            use dispatch <- effect.from
            patients.create_patient(data.id, data.name)
            |> promise.tap(fn(result) {
              dispatch(store.PatientFormSubmitted(result))
            })
            Nil
          }
          #(
            model |> store.set_loading(True) |> store.clear_form_errors(),
            submit_effect,
          )
        }
        Error(errors) -> {
          #(store.Model(..model, form_errors: errors), effect.none())
        }
      }
    }

    store.PatientFormSubmitted(Ok(patient)) -> {
      let new_model =
        model
        |> store.cache_patient(patient)
        |> store.set_loading(False)
        |> store.clear_patient_form()
        |> store.clear_form_errors()
        |> store.set_success("Patient created successfully")
        |> store.set_route(router.PatientDetail(patient.id))
      let nav_effect =
        modem.push(
          router.route_to_path(router.PatientDetail(patient.id)),
          option.None,
          option.None,
        )
      #(new_model, nav_effect)
    }

    store.PatientFormSubmitted(Error(err)) ->
      handle_api_error(model, err, "Failed to create patient")

    // Patient anonymize
    store.AnonymizePatient(id) ->
      load_with_effect(
        model,
        fn() { patients.anonymize_patient(id) },
        store.PatientAnonymized,
      )

    store.PatientAnonymized(Ok(patient)) -> {
      let new_model =
        model
        |> store.cache_patient(patient)
        |> store.set_loading(False)
        |> store.set_success("Patient anonymized successfully")
      #(new_model, effect.none())
    }

    store.PatientAnonymized(Error(err)) ->
      handle_api_error(model, err, "Failed to anonymize patient")

    // Delete patient
    store.DeletePatient(id) -> {
      let delete_effect = {
        use dispatch <- effect.from
        patients.delete_patient(id)
        |> promise.tap(fn(result) { dispatch(store.PatientDeleted(result)) })
        Nil
      }
      #(store.set_loading(model, True), delete_effect)
    }

    store.PatientDeleted(Ok(_)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_success("Patient deleted successfully")
        |> store.set_route(router.Patients)
      #(new_model, effect.batch([
        modem.push("/patients", option.None, option.None),
        dispatch_msg(store.LoadPatients),
      ]))
    }

    store.PatientDeleted(Error(err)) ->
      handle_api_error(model, err, "Failed to delete patient")

    // Delete study
    store.DeleteStudy(study_uid) -> {
      let delete_effect = {
        use dispatch <- effect.from
        studies.delete_study(study_uid)
        |> promise.tap(fn(result) { dispatch(store.StudyDeleted(result)) })
        Nil
      }
      #(store.set_loading(model, True), delete_effect)
    }

    store.StudyDeleted(Ok(_)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_success("Study deleted successfully")
        |> store.set_route(router.Studies)
      #(new_model, effect.batch([
        modem.push("/studies", option.None, option.None),
        dispatch_msg(store.LoadStudies),
      ]))
    }

    store.StudyDeleted(Error(err)) ->
      handle_api_error(model, err, "Failed to delete study")

    // Modal actions
    store.OpenModal(content) -> {
      #(
        store.Model(..model, modal_open: True, modal_content: content),
        effect.none(),
      )
    }

    store.CloseModal -> {
      #(
        store.Model(..model, modal_open: False, modal_content: store.NoModal),
        effect.none(),
      )
    }

    store.ConfirmModalAction -> {
      let close_model =
        store.Model(..model, modal_open: False, modal_content: store.NoModal)
      case model.modal_content {
        store.ConfirmDelete("patient", id) ->
          #(close_model, dispatch_msg(store.DeletePatient(id)))
        store.ConfirmDelete("study", uid) ->
          #(close_model, dispatch_msg(store.DeleteStudy(uid)))
        _ -> #(close_model, effect.none())
      }
    }

    // Data loading - Study Detail
    store.LoadStudyDetail(id) ->
      load_with_effect(
        model,
        fn() { studies.get_study(id) },
        store.StudyDetailLoaded,
      )

    store.StudyDetailLoaded(Ok(study)) -> {
      let new_model =
        model
        |> store.cache_study(study)
        |> store.set_loading(False)
      #(new_model, effect.none())
    }

    store.StudyDetailLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load study")

    // Data loading - Series Detail
    store.LoadSeriesDetail(id) ->
      load_with_effect(
        model,
        fn() { series.get_series(id) },
        store.SeriesDetailLoaded,
      )

    store.SeriesDetailLoaded(Ok(s)) -> {
      let new_model =
        model
        |> store.cache_series(s)
        |> store.set_loading(False)
      #(new_model, effect.none())
    }

    store.SeriesDetailLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load series")

    // PACS operations
    store.SearchPacsStudies(patient_id) -> {
      let search_effect = {
        use dispatch <- effect.from
        dicom.search_patient_studies(patient_id)
        |> promise.tap(fn(result) { dispatch(store.PacsStudiesLoaded(result)) })
        Nil
      }
      #(store.set_pacs_loading(model, True), search_effect)
    }

    store.PacsStudiesLoaded(Ok(pacs_studies)) -> {
      #(store.set_pacs_studies(model, pacs_studies), effect.none())
    }

    store.PacsStudiesLoaded(Error(err)) ->
      handle_api_error(
        store.set_pacs_loading(model, False),
        err,
        "Failed to search PACS",
      )

    store.ImportPacsStudy(study_uid, patient_id) -> {
      let import_effect = {
        use dispatch <- effect.from
        dicom.import_study(study_uid, patient_id)
        |> promise.tap(fn(result) {
          dispatch(store.PacsStudyImported(result))
        })
        Nil
      }
      #(
        store.Model(..model, pacs_importing: Some(study_uid)),
        import_effect,
      )
    }

    store.PacsStudyImported(Ok(study)) -> {
      // Mark study as existing in PACS results
      let updated_pacs =
        list.map(model.pacs_studies, fn(ps) {
          case ps.study.study_instance_uid == study.study_uid {
            True ->
              models.PacsStudyWithSeries(..ps, already_exists: True)
            False -> ps
          }
        })
      let new_model =
        model
        |> store.cache_study(study)
        |> fn(m) {
          store.Model(
            ..m,
            pacs_importing: None,
            pacs_studies: updated_pacs,
          )
        }
        |> store.set_success("Study imported from PACS successfully")
      // Reload patient detail to show new study
      let reload_effect = dispatch_msg(store.LoadPatientDetail(study.patient_id))
      #(new_model, reload_effect)
    }

    store.PacsStudyImported(Error(err)) ->
      handle_api_error(
        store.Model(..model, pacs_importing: None),
        err,
        "Failed to import study from PACS",
      )

    store.ClearPacsResults -> {
      #(store.clear_pacs(model), effect.none())
    }

    // Default case
    _ -> #(model, effect.none())
  }
}

// Helper: standard load-and-dispatch pattern for API calls
fn load_with_effect(
  model: Model,
  api_call: fn() -> promise.Promise(Result(a, types.ApiError)),
  on_result: fn(Result(a, types.ApiError)) -> Msg,
) -> #(Model, Effect(Msg)) {
  let eff = {
    use dispatch <- effect.from
    api_call() |> promise.tap(fn(r) { dispatch(on_result(r)) })
    Nil
  }
  #(store.set_loading(model, True), eff)
}

// Helper: dispatch a message as an effect
fn dispatch_msg(msg: Msg) -> Effect(Msg) {
  use dispatch <- effect.from
  dispatch(msg)
}

/// Handles API errors with automatic session expiry detection.
/// On AuthError: clears user, shows expiry message, redirects to login.
/// On other errors: shows the provided fallback message.
fn handle_api_error(
  model: Model,
  err: types.ApiError,
  fallback_msg: String,
) -> #(Model, Effect(Msg)) {
  case err {
    types.AuthError(_) -> {
      let new_model =
        model
        |> store.clear_user()
        |> store.set_loading(False)
        |> store.set_error(Some("Session expired. Please log in again."))
        |> store.set_route(router.Login)
      #(new_model, modem.push("/login", option.None, option.None))
    }
    _ -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some(fallback_msg))
      #(new_model, effect.none())
    }
  }
}

// View function
pub fn view(model: Model) -> Element(Msg) {
  // Show loading while checking session
  case model.checking_session {
    True -> html.div([], [])
    False -> view_content(model)
  }
}

fn view_content(model: Model) -> Element(Msg) {
  let content = case model.route {
    router.Home -> home.view(model)
    router.Login -> login.view(model)
    router.Register -> register.view(model)
    router.Studies -> studies_list.view(model)
    router.StudyDetail(id) -> study_detail.view(model, id)
    router.StudyViewer(id) -> study_detail.view(model, id)
    router.SeriesDetail(id) -> series_detail.view(model, id)
    router.Records -> records_list.view(model)
    router.RecordDetail(id) -> record_execute.view(model, id)
    router.RecordNew -> html.div([], [html.text("New record page")])
    router.RecordTypeDesign(_id) -> html.div([], [html.text("Record type design page")])
    router.Patients -> patients_list.view(model)
    router.PatientNew -> patient_new.view(model)
    router.PatientDetail(id) -> patient_detail.view(model, id)
    router.Users -> html.div([], [html.text("Users page")])
    router.UserProfile(_id) -> html.div([], [html.text("User profile page")])
    router.AdminDashboard -> admin_page.view(model)
    router.AdminRecordTypes -> record_types_list.view(model)
    router.AdminRecordTypeDetail(name) -> record_type_detail.view(model, name)
    router.AdminRecordTypeEdit(name) -> record_type_edit.view(model, name)
    router.NotFound -> html.div([], [html.text("404 - Page not found")])
  }

  let page = case model.route {
    router.Login | router.Register -> content
    _ -> layout.view(model, content)
  }

  case model.modal_open {
    True -> html.div([], [page, render_modal(model)])
    False -> page
  }
}

fn render_modal(model: Model) -> Element(Msg) {
  let #(title, warning) = case model.modal_content {
    store.ConfirmDelete("patient", id) -> #(
      "Delete Patient",
      "Are you sure you want to delete patient \""
        <> id
        <> "\"? This will permanently delete all associated studies, series, and records. This action cannot be undone.",
    )
    store.ConfirmDelete("study", uid) -> #(
      "Delete Study",
      "Are you sure you want to delete study \""
        <> uid
        <> "\"? This will permanently delete all associated series and records. This action cannot be undone.",
    )
    _ -> #("Confirm", "Are you sure?")
  }

  html.div([attribute.class("modal-backdrop")], [
    html.div([attribute.class("modal")], [
      html.div([attribute.class("modal-header")], [
        html.h3([attribute.class("modal-title")], [html.text(title)]),
      ]),
      html.p([], [html.text(warning)]),
      html.div([attribute.class("modal-footer")], [
        html.button(
          [
            attribute.class("btn btn-secondary"),
            event.on_click(store.CloseModal),
          ],
          [html.text("Cancel")],
        ),
        html.button(
          [
            attribute.class("btn btn-danger"),
            event.on_click(store.ConfirmModalAction),
          ],
          [html.text("Delete")],
        ),
      ]),
    ]),
  ])
}

// Load data for route
fn load_route_data(model: Model, route: Route) -> Effect(Msg) {
  case route, model.user {
    router.Home, Some(models.User(is_superuser: True, ..)) ->
      effect.batch([
        dispatch_msg(store.LoadStudies),
        dispatch_msg(store.LoadRecords),
        dispatch_msg(store.LoadUsers),
      ])
    router.Home, Some(_) -> dispatch_msg(store.LoadRecords)
    router.Home, None -> effect.none()
    router.Studies, Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadStudies)
    router.StudyDetail(id), Some(models.User(is_superuser: True, ..)) ->
      effect.batch([
        dispatch_msg(store.LoadStudyDetail(id)),
        dispatch_msg(store.LoadRecords),
      ])
    router.StudyViewer(id), Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadStudyDetail(id))
    router.SeriesDetail(id), Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadSeriesDetail(id))
    router.Records, _ -> dispatch_msg(store.LoadRecords)
    router.RecordDetail(id), Some(_) ->
      effect.batch([
        dispatch_msg(store.LoadRecordDetail(id)),
        dispatch_msg(store.SlicerPing),
      ])
    router.Patients, Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadPatients)
    router.PatientDetail(id), Some(models.User(is_superuser: True, ..)) ->
      effect.batch([
        dispatch_msg(store.LoadPatientDetail(id)),
        dispatch_msg(store.LoadRecords),
      ])
    router.PatientNew, _ -> effect.none()
    router.Users, Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadUsers)
    router.AdminDashboard, Some(_) ->
      effect.batch([
        dispatch_msg(store.LoadAdminStats),
        dispatch_msg(store.LoadRecords),
        dispatch_msg(store.LoadUsers),
      ])
    router.AdminRecordTypes, Some(_) ->
      dispatch_msg(store.LoadRecordTypeStats)
    router.AdminRecordTypeDetail(_), Some(_) ->
      effect.batch([
        dispatch_msg(store.LoadRecordTypeStats),
        dispatch_msg(store.LoadRecords),
      ])
    router.AdminRecordTypeEdit(name), Some(_) ->
      dispatch_msg(store.LoadRecordTypeForEdit(name))
    _, _ -> effect.none()
  }
}
