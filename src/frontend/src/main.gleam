// Main Lustre application
import api/admin
import api/auth
import api/dicom
import api/models
import api/patients
import api/records
import api/series
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
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import modem
import pages/admin as admin_page
import pages/record_types/detail as record_type_detail
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

    store.StudiesLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load studies"))
      #(new_model, effect.none())
    }

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

    store.RecordsLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load records"))
      #(new_model, effect.none())
    }

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

    store.UsersLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load users"))
      #(new_model, effect.none())
    }

    // Data loading - Admin Stats
    store.LoadAdminStats ->
      load_with_effect(model, admin.get_admin_stats, store.AdminStatsLoaded)

    store.AdminStatsLoaded(Ok(stats)) -> {
      let new_model =
        store.Model(..model, admin_stats: Some(stats), loading: False)
      #(new_model, effect.none())
    }

    store.AdminStatsLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load admin statistics"))
      #(new_model, effect.none())
    }

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

    store.RecordTypeStatsLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load record type statistics"))
      #(new_model, effect.none())
    }

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

    store.AdminAssignUserResult(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to assign user to record"))
      #(new_model, effect.none())
    }

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

    store.RecordDetailLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load record"))
      #(new_model, effect.none())
    }

    // Formosh form events
    store.FormSubmitSuccess(record_id) -> {
      #(
        store.set_success(model, "Record data submitted successfully"),
        dispatch_msg(store.LoadRecordDetail(record_id)),
      )
    }

    store.FormSubmitError(error) -> {
      #(store.set_error(model, Some(error)), effect.none())
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

    store.PatientsLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load patients"))
      #(new_model, effect.none())
    }

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

    store.PatientDetailLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load patient"))
      #(new_model, effect.none())
    }

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

    store.PatientFormSubmitted(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to create patient"))
      #(new_model, effect.none())
    }

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

    store.PatientAnonymized(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to anonymize patient"))
      #(new_model, effect.none())
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

    store.StudyDetailLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load study"))
      #(new_model, effect.none())
    }

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

    store.SeriesDetailLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_loading(False)
        |> store.set_error(Some("Failed to load series"))
      #(new_model, effect.none())
    }

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

    store.PacsStudiesLoaded(Error(_err)) -> {
      let new_model =
        model
        |> store.set_pacs_loading(False)
        |> store.set_error(Some("Failed to search PACS"))
      #(new_model, effect.none())
    }

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

    store.PacsStudyImported(Error(_err)) -> {
      let new_model =
        model
        |> fn(m) { store.Model(..m, pacs_importing: None) }
        |> store.set_error(Some("Failed to import study from PACS"))
      #(new_model, effect.none())
    }

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
    router.NotFound -> html.div([], [html.text("404 - Page not found")])
  }

  case model.route {
    router.Login | router.Register -> content
    _ -> layout.view(model, content)
  }
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
    router.SeriesDetail(id), Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadSeriesDetail(id))
    router.Records, _ -> dispatch_msg(store.LoadRecords)
    router.RecordDetail(id), Some(_) ->
      dispatch_msg(store.LoadRecordDetail(id))
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
    _, _ -> effect.none()
  }
}
