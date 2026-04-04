// Main Lustre application
import api/admin
import api/auth
import api/dicomweb
import api/info
import api/models
import api/patients
import api/records
import api/studies
import api/types
import api/users
import components/layout
import formosh/component as formosh_component
import gleam/dict
import gleam/dynamic/decode
import gleam/int
import gleam/javascript/promise
import gleam/list
import gleam/option.{None, Some}
import gleam/string
import gleam/uri.{type Uri}
import utils/logger
import lustre
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import modem
import pages/admin as admin_page
import pages/home
import pages/login
import pages/patients/detail as patient_detail
import pages/patients/list as patients_list
import pages/patients/new as patient_new
import pages/record_types/detail as record_type_detail
import pages/record_types/edit as record_type_edit
import pages/record_types/list as record_types_list
import pages/records/execute as record_execute
import pages/records/list as records_list
import pages/records/new as record_new
import pages/register
import pages/series/detail as series_detail
import pages/studies/detail as study_detail
import pages/studies/list as studies_list
import plinth/browser/window
import plinth/javascript/global
import router.{type Route}
import shared.{type OutMsg}
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

  // Fetch project branding info
  let fetch_info_effect = {
    use dispatch <- effect.from
    info.get_project_info()
    |> promise.tap(fn(result) { dispatch(store.ProjectInfoLoaded(result)) })
    Nil
  }

  #(
    model_with_route,
    effect.batch([
      modem.init(on_url_change),
      register_formosh_effect,
      check_session_effect,
      fetch_info_effect,
    ]),
  )
}

// Handle URL changes from modem
fn on_url_change(uri: Uri) -> Msg {
  logger.debug("router", "on_url_change fired, uri: " <> string.inspect(uri))
  let route = router.parse_route(uri)
  logger.debug("router", "parsed route: " <> string.inspect(route))
  store.OnRouteChange(route)
}

// Update function — wrapper with auto-dismiss for notifications
pub fn update(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  let #(new_model, eff) = update_inner(model, msg)
  let dismiss_effects = auto_dismiss_effects(model, new_model)
  #(new_model, effect.batch([eff, dismiss_effects]))
}

// Auto-dismiss helper: schedule clearing notifications after 5 seconds
fn auto_dismiss_effect(msg: Msg, delay_ms: Int) -> Effect(Msg) {
  use dispatch <- effect.from
  let _ = global.set_timeout(delay_ms, fn() { dispatch(msg) })
  Nil
}

// Detect new error/success messages and schedule auto-dismiss
fn auto_dismiss_effects(old: Model, new: Model) -> Effect(Msg) {
  let success_eff = case old.success_message, new.success_message {
    None, Some(_) -> auto_dismiss_effect(store.ClearSuccessMessage, 5000)
    Some(old_msg), Some(new_msg) if old_msg != new_msg ->
      auto_dismiss_effect(store.ClearSuccessMessage, 5000)
    _, _ -> effect.none()
  }
  let error_eff = case old.error, new.error {
    None, Some(_) -> auto_dismiss_effect(store.ClearError, 5000)
    Some(old_msg), Some(new_msg) if old_msg != new_msg ->
      auto_dismiss_effect(store.ClearError, 5000)
    _, _ -> effect.none()
  }
  effect.batch([success_eff, error_eff])
}

fn update_inner(model: Model, msg: Msg) -> #(Model, Effect(Msg)) {
  case msg {
    // Routing
    store.OnRouteChange(route) -> {
      logger.debug(
        "router",
        "OnRouteChange route: "
        <> string.inspect(route)
        <> ", checking_session: "
        <> string.inspect(model.checking_session)
        <> ", user: "
        <> string.inspect(model.user),
      )
      // Cleanup current page (e.g. slicer ping timer) + stop preload timer
      let page_cleanup = cleanup_current_page(model)
      let preload_cleanup = stop_preload_timer(model)
      let cleanup_effect = effect.batch([page_cleanup, preload_cleanup])
      let new_model =
        store.Model(
          ..store.set_route(model, route),
          preload_timer: None,
          modal_open: case model.preload_timer {
            Some(_) -> False
            None -> model.modal_open
          },
          modal_content: case model.preload_timer {
            Some(_) -> store.NoModal
            None -> model.modal_content
          },
        )

      // Don't redirect while session check is in progress
      case model.checking_session {
        True -> #(new_model, cleanup_effect)
        False -> {
          // Check authentication requirement
          let is_auth_page = route == router.Login || route == router.Register
          case router.requires_auth(route), model.user, is_auth_page {
            True, None, _ -> {
              // Redirect to login if auth required
              #(
                store.set_route(model, router.Login),
                effect.batch([
                  cleanup_effect,
                  modem.push(router.route_to_path(router.Login), option.None, option.None),
                ]),
              )
            }
            False, Some(_), True -> {
              // Redirect from login/register if already authenticated
              #(
                store.set_route(model, router.Home),
                effect.batch([
                  cleanup_effect,
                  modem.push(router.route_to_path(router.Home), option.None, option.None),
                ]),
              )
            }
            _, _, _ ->
              case router.requires_admin_role(route), model.user {
                True, Some(models.User(is_superuser: False, ..)) -> #(
                  store.set_route(model, router.Home),
                  effect.batch([
                    cleanup_effect,
                    modem.push(router.route_to_path(router.Home), option.None, option.None),
                  ]),
                )
                _, _ -> {
                  // Initialize page model for modular pages
                  let #(new_model, page_init_eff) =
                    init_page_for_route(new_model, route)
                  #(
                    new_model,
                    effect.batch([
                      cleanup_effect,
                      load_route_data(new_model, route),
                      page_init_eff,
                    ]),
                  )
                }
              }
          }
        }
      }
    }

    store.Navigate(route) -> {
      logger.debug("router", "Navigate route: " <> string.inspect(route))
      // Use Modem to update URL without page reload
      let path = router.route_to_path(route)
      logger.debug("router", "pushing path: " <> path)
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
            False, Some(_), True -> #(
              store.set_route(new_model, router.Home),
              modem.push(router.route_to_path(router.Home), option.None, option.None),
            )
            _, _, _ ->
              case router.requires_admin_role(route), new_model.user {
                True, Some(models.User(is_superuser: False, ..)) -> #(
                  store.set_route(new_model, router.Home),
                  modem.push(router.route_to_path(router.Home), option.None, option.None),
                )
                _, _ -> {
                  let #(new_model, page_init_eff) =
                    init_page_for_route(new_model, route)
                  #(
                    new_model,
                    effect.batch([
                      load_route_data(new_model, route),
                      page_init_eff,
                    ]),
                  )
                }
              }
          }
        }
        Error(_) -> {
          // No valid session - redirect to login if on protected route
          let new_model = store.Model(..model, checking_session: False)
          case router.requires_auth(model.route) {
            True -> #(
              store.set_route(new_model, router.Login),
              modem.push(router.route_to_path(router.Login), option.None, option.None),
            )
            False -> {
              let #(new_model, page_init_eff) =
                init_page_for_route(new_model, model.route)
              #(new_model, page_init_eff)
            }
          }
        }
      }
    }

    // Auth page delegation
    store.LoginMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.LoginPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { login.update(m, page_msg, s) },
        store.LoginPage,
        store.LoginMsg,
      )

    store.RegisterMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.RegisterPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { register.update(m, page_msg, s) },
        store.RegisterPage,
        store.RegisterMsg,
      )

    store.Logout -> {
      let logout_effect = {
        use dispatch <- effect.from
        auth.logout()
        |> promise.tap(fn(_) { dispatch(store.LogoutComplete) })
        Nil
      }
      #(store.reset_for_logout(model), logout_effect)
    }

    store.LogoutComplete -> {
      #(
        store.set_route(model, router.Login),
        modem.push(router.route_to_path(router.Login), option.None, option.None),
      )
    }

    // UI Messages
    store.ClearError -> {
      #(store.Model(..model, error: None), effect.none())
    }

    store.ClearSuccessMessage -> {
      #(store.Model(..model, success_message: None), effect.none())
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
      let new_model = store.Model(..model, users: users_dict, loading: False)
      #(new_model, effect.none())
    }

    store.UsersLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load users")

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
      // Auto-assign user to unassigned pending/inwork records
      let assign_effect = case record.user_id, record.status, model.user {
        None, types.Pending, Some(models.User(id: uid, ..))
        | None, types.InWork, Some(models.User(id: uid, ..))
        -> {
          case record.id {
            Some(rid) -> {
              use dispatch <- effect.from
              records.assign_record_user(rid, uid)
              |> promise.tap(fn(result) {
                dispatch(store.AutoAssignResult(result))
              })
              Nil
            }
            None -> effect.none()
          }
        }
        _, _, _ -> effect.none()
      }
      #(new_model, assign_effect)
    }

    store.AutoAssignResult(Ok(record)) -> {
      #(store.cache_record(model, record), effect.none())
    }

    store.AutoAssignResult(Error(_)) -> {
      // Silently ignore — admin can still work without assignment
      #(model, effect.none())
    }

    store.RecordDetailLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load record")

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

    // Record types loading (shared — used by record_new page and admin)
    store.LoadRecordTypes ->
      load_with_effect(model, records.get_record_types, store.RecordTypesLoaded)

    store.RecordTypesLoaded(Ok(rt_list)) -> {
      let rt_dict =
        list.fold(rt_list, model.record_types, fn(acc, rt) {
          dict.insert(acc, rt.name, rt)
        })
      #(store.Model(..model, record_types: rt_dict, loading: False), effect.none())
    }

    store.RecordTypesLoaded(Error(err)) ->
      handle_api_error(model, err, "Failed to load record types")

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
        store.ConfirmDelete("patient", _id) -> #(
          close_model,
          dispatch_msg(store.PatientDetailMsg(patient_detail.Delete)),
        )
        store.ConfirmDelete("study", _uid) -> #(
          close_model,
          dispatch_msg(store.StudyDetailMsg(study_detail.Delete)),
        )
        _ -> #(close_model, effect.none())
      }
    }

    // Project info
    store.ProjectInfoLoaded(Ok(project_info)) -> {
      #(
        store.Model(
          ..model,
          project_name: project_info.project_name,
          project_description: project_info.project_description,
        ),
        effect.none(),
      )
    }

    store.ProjectInfoLoaded(Error(_)) -> {
      // Silently ignore — defaults are already set
      #(model, effect.none())
    }

    // Admin page delegation
    store.AdminMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.AdminPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { admin_page.update(m, page_msg, s) },
        store.AdminPage,
        store.AdminMsg,
      )

    // Patient page delegation
    store.PatientsListMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.PatientsListPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { patients_list.update(m, page_msg, s) },
        store.PatientsListPage,
        store.PatientsListMsg,
      )

    store.PatientDetailMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.PatientDetailPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { patient_detail.update(m, page_msg, s) },
        store.PatientDetailPage,
        store.PatientDetailMsg,
      )

    store.PatientNewMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.PatientNewPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { patient_new.update(m, page_msg, s) },
        store.PatientNewPage,
        store.PatientNewMsg,
      )

    // Record page delegation
    store.RecordsListMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.RecordsListPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { records_list.update(m, page_msg, s) },
        store.RecordsListPage,
        store.RecordsListMsg,
      )

    store.RecordExecuteMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.RecordExecutePage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { record_execute.update(m, page_msg, s) },
        store.RecordExecutePage,
        store.RecordExecuteMsg,
      )

    store.RecordNewMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.RecordNewPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { record_new.update(m, page_msg, s) },
        store.RecordNewPage,
        store.RecordNewMsg,
      )

    // Study/Series page delegation
    store.StudiesListMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.StudiesListPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { studies_list.update(m, page_msg, s) },
        store.StudiesListPage,
        store.StudiesListMsg,
      )

    store.StudyDetailMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.StudyDetailPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { study_detail.update(m, page_msg, s) },
        store.StudyDetailPage,
        store.StudyDetailMsg,
      )

    store.SeriesDetailMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.SeriesDetailPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { series_detail.update(m, page_msg, s) },
        store.SeriesDetailPage,
        store.SeriesDetailMsg,
      )

    // Record type page delegation
    store.RecordTypesListMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.RecordTypesListPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { record_types_list.update(m, page_msg, s) },
        store.RecordTypesListPage,
        store.RecordTypesListMsg,
      )

    store.RecordTypeDetailMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.RecordTypeDetailPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { record_type_detail.update(m, page_msg, s) },
        store.RecordTypeDetailPage,
        store.RecordTypeDetailMsg,
      )

    store.RecordTypeEditMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.RecordTypeEditPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { record_type_edit.update(m, page_msg, s) },
        store.RecordTypeEditPage,
        store.RecordTypeEditMsg,
      )

    // Home page delegation
    store.HomeMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.HomePage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { home.update(m, page_msg, s) },
        store.HomePage,
        store.HomeMsg,
      )

    store.SetError(error) -> {
      #(store.set_error(model, error), effect.none())
    }

    // Preload
    store.StartPreload(viewer_url, study_uid) -> {
      let preload_effect = {
        use dispatch <- effect.from
        dicomweb.preload_study(study_uid)
        |> promise.tap(fn(result) {
          dispatch(store.PreloadStarted(viewer_url, case result {
            Ok(data) ->
              case decode.run(data, decode.at(["task_id"], decode.string)) {
                Ok(tid) -> tid
                Error(_) -> ""
              }
            Error(_) -> ""
          }, study_uid))
        })
        Nil
      }
      #(
        store.Model(
          ..model,
          modal_open: True,
          modal_content: store.PreloadProgress(
            viewer_url: viewer_url,
            task_id: "",
            study_uid: study_uid,
            received: 0,
            total: None,
            status: "starting",
          ),
        ),
        preload_effect,
      )
    }

    store.PreloadStarted(viewer_url, task_id, study_uid) -> {
      case task_id {
        "" ->
          // Failed to start — just open viewer directly
          #(
            store.Model(..model, modal_open: False, modal_content: store.NoModal),
            effect.from(fn(_dispatch) {
              window.open(viewer_url, "_blank", "")
              Nil
            }),
          )
        _ -> {
          let timer_effect = {
            use dispatch <- effect.from
            let timer_id =
              global.set_interval(2000, fn() {
                dispatch(store.PreloadPollTick(task_id, viewer_url, study_uid))
              })
            dispatch(store.PreloadPollTick(task_id, viewer_url, study_uid))
            dispatch(store.SetPreloadTimer(timer_id))
          }
          #(
            store.Model(
              ..model,
              modal_content: store.PreloadProgress(
                viewer_url: viewer_url,
                task_id: task_id,
                study_uid: study_uid,
                received: 0,
                total: None,
                status: "starting",
              ),
            ),
            timer_effect,
          )
        }
      }
    }

    store.SetPreloadTimer(timer_id) -> {
      #(store.Model(..model, preload_timer: Some(timer_id)), effect.none())
    }

    store.PreloadPollTick(task_id, viewer_url, study_uid) -> {
      let poll_effect = {
        use dispatch <- effect.from
        dicomweb.preload_progress(study_uid, task_id)
        |> promise.tap(fn(result) {
          dispatch(store.PreloadProgressUpdate(
            task_id, viewer_url, study_uid, result,
          ))
        })
        Nil
      }
      #(model, poll_effect)
    }

    store.PreloadProgressUpdate(task_id, viewer_url, study_uid, Ok(data)) -> {
      let status =
        decode.run(data, decode.at(["status"], decode.string))
        |> option.from_result
        |> option.unwrap("unknown")
      let received =
        decode.run(data, decode.at(["received"], decode.int))
        |> option.from_result
        |> option.unwrap(0)
      let total =
        decode.run(data, decode.at(["total"], decode.int))
        |> option.from_result

      case status {
        "ready" -> {
          // Stop timer, open viewer, close modal
          let stop_effect = stop_preload_timer(model)
          let open_effect =
            effect.from(fn(_dispatch) {
              window.open(viewer_url, "_blank", "")
              Nil
            })
          #(
            store.Model(
              ..model,
              modal_open: False,
              modal_content: store.NoModal,
              preload_timer: None,
            ),
            effect.batch([stop_effect, open_effect]),
          )
        }
        "error" -> {
          let stop_effect = stop_preload_timer(model)
          let error_msg =
            decode.run(data, decode.at(["error"], decode.string))
            |> option.from_result
            |> option.unwrap("Preload failed")
          #(
            store.Model(
              ..model,
              modal_open: False,
              modal_content: store.NoModal,
              preload_timer: None,
            )
              |> store.set_error(Some(error_msg)),
            stop_effect,
          )
        }
        _ ->
          #(
            store.Model(
              ..model,
              modal_content: store.PreloadProgress(
                viewer_url: viewer_url,
                task_id: task_id,
                study_uid: study_uid,
                received: received,
                total: total,
                status: status,
              ),
            ),
            effect.none(),
          )
      }
    }

    store.PreloadProgressUpdate(_, _, _, Error(err)) -> {
      logger.warn(
        "preload",
        "Progress poll failed: " <> string.inspect(err),
      )
      #(model, effect.none())
    }

    store.CancelPreload -> {
      let stop_effect = stop_preload_timer(model)
      #(
        store.Model(
          ..model,
          modal_open: False,
          modal_content: store.NoModal,
          preload_timer: None,
        ),
        stop_effect,
      )
    }
  }
}

/// Generic page delegation helper — reduces boilerplate for MVU page updates
fn delegate_page_update(
  model: Model,
  get_page: fn(store.PageModel) -> Result(page_model, Nil),
  do_update: fn(page_model, shared.Shared) -> #(page_model, Effect(page_msg), List(OutMsg)),
  wrap_page: fn(page_model) -> store.PageModel,
  wrap_msg: fn(page_msg) -> Msg,
) -> #(Model, Effect(Msg)) {
  case get_page(model.page) {
    Ok(page_model) -> {
      let #(new_page, eff, out_msgs) =
        do_update(page_model, build_shared(model))
      let model =
        store.Model(..model, page: wrap_page(new_page))
      let #(model, out_effects) = apply_out_msgs(model, out_msgs)
      #(
        model,
        effect.batch([
          effect.map(eff, wrap_msg),
          out_effects,
        ]),
      )
    }
    Error(_) -> #(model, effect.none())
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

fn stop_preload_timer(model: Model) -> Effect(Msg) {
  case model.preload_timer {
    Some(timer_id) ->
      effect.from(fn(_dispatch) { global.clear_interval(timer_id) })
    None -> effect.none()
  }
}

fn build_shared(model: Model) -> shared.Shared {
  shared.Shared(
    user: model.user,
    route: model.route,
    project_name: model.project_name,
    project_description: model.project_description,
    studies: model.studies,
    series: model.series,
    records: model.records,
    record_types: model.record_types,
    patients: model.patients,
    users: model.users,
    record_type_stats: model.record_type_stats,
  )
}

/// Cleanup current page before route change (e.g. slicer ping timer)
fn cleanup_current_page(model: Model) -> Effect(Msg) {
  case model.page {
    store.RecordExecutePage(page_model) ->
      effect.map(record_execute.cleanup(page_model), store.RecordExecuteMsg)
    _ -> effect.none()
  }
}

fn init_page_for_route(model: Model, route: Route) -> #(Model, Effect(Msg)) {
  let shared = build_shared(model)
  case route {
    router.AdminDashboard -> {
      let #(page_model, page_eff) = admin_page.init(shared)
      #(
        store.Model(..model, page: store.AdminPage(page_model)),
        effect.map(page_eff, store.AdminMsg),
      )
    }
    router.Login -> {
      let #(page_model, page_eff) = login.init(shared)
      #(
        store.Model(..model, page: store.LoginPage(page_model)),
        effect.map(page_eff, store.LoginMsg),
      )
    }
    router.Register -> {
      let #(page_model, page_eff) = register.init(shared)
      #(
        store.Model(..model, page: store.RegisterPage(page_model)),
        effect.map(page_eff, store.RegisterMsg),
      )
    }
    router.Patients -> {
      let #(page_model, page_eff) = patients_list.init(shared)
      #(
        store.Model(..model, page: store.PatientsListPage(page_model)),
        effect.map(page_eff, store.PatientsListMsg),
      )
    }
    router.PatientDetail(id) -> {
      let #(page_model, page_eff) = patient_detail.init(id, shared)
      #(
        store.Model(..model, page: store.PatientDetailPage(page_model)),
        effect.map(page_eff, store.PatientDetailMsg),
      )
    }
    router.PatientNew -> {
      let #(page_model, page_eff) = patient_new.init(shared)
      #(
        store.Model(..model, page: store.PatientNewPage(page_model)),
        effect.map(page_eff, store.PatientNewMsg),
      )
    }
    router.Records -> {
      let #(page_model, page_eff) = records_list.init(shared)
      #(
        store.Model(..model, page: store.RecordsListPage(page_model)),
        effect.map(page_eff, store.RecordsListMsg),
      )
    }
    router.RecordDetail(id) -> {
      let #(page_model, page_eff) = record_execute.init(id, shared)
      #(
        store.Model(..model, page: store.RecordExecutePage(page_model)),
        effect.map(page_eff, store.RecordExecuteMsg),
      )
    }
    router.RecordNew -> {
      let #(page_model, page_eff) = record_new.init(shared)
      #(
        store.Model(..model, page: store.RecordNewPage(page_model)),
        effect.map(page_eff, store.RecordNewMsg),
      )
    }
    router.Studies -> {
      let #(page_model, page_eff) = studies_list.init(shared)
      #(
        store.Model(..model, page: store.StudiesListPage(page_model)),
        effect.map(page_eff, store.StudiesListMsg),
      )
    }
    router.StudyDetail(id) | router.StudyViewer(id) -> {
      let #(page_model, page_eff) = study_detail.init(id, shared)
      #(
        store.Model(..model, page: store.StudyDetailPage(page_model)),
        effect.map(page_eff, store.StudyDetailMsg),
      )
    }
    router.SeriesDetail(id) -> {
      let #(page_model, page_eff) = series_detail.init(id, shared)
      #(
        store.Model(..model, page: store.SeriesDetailPage(page_model)),
        effect.map(page_eff, store.SeriesDetailMsg),
      )
    }
    router.AdminRecordTypes -> {
      let #(page_model, page_eff) = record_types_list.init(shared)
      #(
        store.Model(..model, page: store.RecordTypesListPage(page_model)),
        effect.map(page_eff, store.RecordTypesListMsg),
      )
    }
    router.AdminRecordTypeDetail(name) -> {
      let #(page_model, page_eff) = record_type_detail.init(name, shared)
      #(
        store.Model(..model, page: store.RecordTypeDetailPage(page_model)),
        effect.map(page_eff, store.RecordTypeDetailMsg),
      )
    }
    router.AdminRecordTypeEdit(name) -> {
      let #(page_model, page_eff) = record_type_edit.init(name, shared)
      #(
        store.Model(..model, page: store.RecordTypeEditPage(page_model)),
        effect.map(page_eff, store.RecordTypeEditMsg),
      )
    }
    router.Home -> {
      let #(page_model, page_eff) = home.init(shared)
      #(
        store.Model(..model, page: store.HomePage(page_model)),
        effect.map(page_eff, store.HomeMsg),
      )
    }
    _ -> #(store.Model(..model, page: store.NoPage), effect.none())
  }
}

fn apply_out_msgs(
  model: Model,
  msgs: List(OutMsg),
) -> #(Model, Effect(Msg)) {
  let #(final_model, effs) =
    list.fold(msgs, #(model, []), fn(acc, out_msg) {
      let #(m, eff_list) = acc
      case out_msg {
        shared.ShowSuccess(text) ->
          #(store.set_success(m, text), eff_list)
        shared.ShowError(text) ->
          #(store.set_error(m, option.Some(text)), eff_list)
        shared.Navigate(route) ->
          #(m, [modem.push(router.route_to_path(route), option.None, option.None), ..eff_list])
        shared.SetLoading(loading) ->
          #(store.set_loading(m, loading), eff_list)
        shared.CacheRecord(record) ->
          #(store.cache_record(m, record), eff_list)
        shared.CacheStudy(study) ->
          #(store.cache_study(m, study), eff_list)
        shared.CachePatient(patient) ->
          #(store.cache_patient(m, patient), eff_list)
        shared.CacheRecordType(rt) ->
          #(store.cache_record_type(m, rt), eff_list)
        shared.CacheSeries(s) ->
          #(store.cache_series(m, s), eff_list)
        shared.ReloadRecords ->
          #(m, [dispatch_msg(store.LoadRecords), ..eff_list])
        shared.ReloadStudies ->
          #(m, [dispatch_msg(store.LoadStudies), ..eff_list])
        shared.ReloadUsers ->
          #(m, [dispatch_msg(store.LoadUsers), ..eff_list])
        shared.ReloadPatients ->
          #(m, [dispatch_msg(store.LoadPatients), ..eff_list])
        shared.ReloadRecordTypes ->
          #(m, [dispatch_msg(store.LoadRecordTypes), ..eff_list])
        shared.ReloadRecordTypeStats ->
          #(m, [dispatch_msg(store.LoadRecordTypeStats), ..eff_list])
        shared.ReloadPatient(id) ->
          #(m, [dispatch_msg(store.LoadPatientDetail(id)), ..eff_list])
        shared.ReloadRecord(id) ->
          #(m, [dispatch_msg(store.LoadRecordDetail(id)), ..eff_list])
        shared.OpenDeleteConfirm(resource, id) ->
          #(
            store.Model(..m, modal_open: True, modal_content: store.ConfirmDelete(resource, id)),
            eff_list,
          )
        shared.SetUser(user) ->
          #(store.set_user(m, user), eff_list)
        shared.Logout ->
          #(m, [dispatch_msg(store.Logout), ..eff_list])
        shared.StartPreload(viewer_url, study_uid) ->
          #(m, [dispatch_msg(store.StartPreload(viewer_url, study_uid)), ..eff_list])
      }
    })
  #(final_model, effect.batch(list.reverse(effs)))
}

/// Handles API errors with automatic session expiry detection.
fn handle_api_error(
  model: Model,
  err: types.ApiError,
  fallback_msg: String,
) -> #(Model, Effect(Msg)) {
  case err {
    types.AuthError(msg) -> {
      logger.error(
        "auth",
        "session error - msg: "
        <> msg
        <> ", route: "
        <> string.inspect(model.route)
        <> ", user_id: "
        <> case model.user {
          Some(user) -> user.id
          None -> "none"
        }
        <> ", loading: "
        <> string.inspect(model.loading),
      )
      let new_model =
        model
        |> store.reset_for_logout()
        |> store.set_loading(False)
        |> store.set_error(Some("Session expired. Please log in again."))
        |> store.set_route(router.Login)
      #(new_model, modem.push(router.route_to_path(router.Login), option.None, option.None))
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
    router.Home ->
      case model.page {
        store.HomePage(page_model) ->
          element.map(
            home.view(page_model, build_shared(model)),
            store.HomeMsg,
          )
        _ -> loading_placeholder()
      }
    router.Login ->
      case model.page {
        store.LoginPage(page_model) ->
          element.map(
            login.view(page_model, build_shared(model)),
            store.LoginMsg,
          )
        _ -> loading_placeholder()
      }
    router.Register ->
      case model.page {
        store.RegisterPage(page_model) ->
          element.map(
            register.view(page_model, build_shared(model)),
            store.RegisterMsg,
          )
        _ -> loading_placeholder()
      }
    router.Studies ->
      case model.page {
        store.StudiesListPage(page_model) ->
          element.map(
            studies_list.view(page_model, build_shared(model)),
            store.StudiesListMsg,
          )
        _ -> loading_placeholder()
      }
    router.StudyDetail(_) | router.StudyViewer(_) ->
      case model.page {
        store.StudyDetailPage(page_model) ->
          element.map(
            study_detail.view(page_model, build_shared(model)),
            store.StudyDetailMsg,
          )
        _ -> loading_placeholder()
      }
    router.SeriesDetail(_) ->
      case model.page {
        store.SeriesDetailPage(page_model) ->
          element.map(
            series_detail.view(page_model, build_shared(model)),
            store.SeriesDetailMsg,
          )
        _ -> loading_placeholder()
      }
    router.Records ->
      case model.page {
        store.RecordsListPage(page_model) ->
          element.map(
            records_list.view(page_model, build_shared(model)),
            store.RecordsListMsg,
          )
        _ -> loading_placeholder()
      }
    router.RecordDetail(_) ->
      case model.page {
        store.RecordExecutePage(page_model) ->
          element.map(
            record_execute.view(page_model, build_shared(model)),
            store.RecordExecuteMsg,
          )
        _ -> loading_placeholder()
      }
    router.RecordNew ->
      case model.page {
        store.RecordNewPage(page_model) ->
          element.map(
            record_new.view(page_model, build_shared(model)),
            store.RecordNewMsg,
          )
        _ -> loading_placeholder()
      }
    router.RecordTypeDesign(_id) ->
      html.div([], [html.text("Record type design page")])
    router.Patients ->
      case model.page {
        store.PatientsListPage(page_model) ->
          element.map(
            patients_list.view(page_model, build_shared(model)),
            store.PatientsListMsg,
          )
        _ -> loading_placeholder()
      }
    router.PatientNew ->
      case model.page {
        store.PatientNewPage(page_model) ->
          element.map(
            patient_new.view(page_model, build_shared(model)),
            store.PatientNewMsg,
          )
        _ -> loading_placeholder()
      }
    router.PatientDetail(_) ->
      case model.page {
        store.PatientDetailPage(page_model) ->
          element.map(
            patient_detail.view(page_model, build_shared(model)),
            store.PatientDetailMsg,
          )
        _ -> loading_placeholder()
      }
    router.Users -> html.div([], [html.text("Users page")])
    router.UserProfile(_id) -> html.div([], [html.text("User profile page")])
    router.AdminDashboard ->
      case model.page {
        store.AdminPage(page_model) ->
          element.map(
            admin_page.view(page_model, build_shared(model)),
            store.AdminMsg,
          )
        _ -> loading_placeholder()
      }
    router.AdminRecordTypes ->
      case model.page {
        store.RecordTypesListPage(page_model) ->
          element.map(
            record_types_list.view(page_model, build_shared(model)),
            store.RecordTypesListMsg,
          )
        _ -> loading_placeholder()
      }
    router.AdminRecordTypeDetail(_) ->
      case model.page {
        store.RecordTypeDetailPage(page_model) ->
          element.map(
            record_type_detail.view(page_model, build_shared(model)),
            store.RecordTypeDetailMsg,
          )
        _ -> loading_placeholder()
      }
    router.AdminRecordTypeEdit(_) ->
      case model.page {
        store.RecordTypeEditPage(page_model) ->
          element.map(
            record_type_edit.view(page_model, build_shared(model)),
            store.RecordTypeEditMsg,
          )
        _ -> loading_placeholder()
      }
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

fn loading_placeholder() -> Element(Msg) {
  html.div([attribute.class("loading")], [
    html.p([], [html.text("Loading...")]),
  ])
}

fn render_modal(model: Model) -> Element(Msg) {
  case model.modal_content {
    store.PreloadProgress(_, _, _, received, total, status) ->
      render_preload_modal(received, total, status)
    _ -> render_confirm_modal(model)
  }
}

fn render_preload_modal(
  received: Int,
  total: option.Option(Int),
  status: String,
) -> Element(Msg) {
  let progress_text = case status {
    "checking_cache" -> "Checking cache..."
    "starting" -> "Starting preload..."
    "fetching" ->
      case total {
        Some(t) ->
          "Received " <> int.to_string(received) <> " of ~" <> int.to_string(t)
        None -> "Received " <> int.to_string(received) <> " images..."
      }
    "ready" -> "Ready!"
    _ -> "Loading..."
  }

  let progress_bar = case total {
    Some(t) if t > 0 -> {
      let pct = { received * 100 } / t
      let width = int.to_string(int.min(pct, 100)) <> "%"
      html.div([attribute.class("progress-bar-container")], [
        html.div(
          [
            attribute.class("progress-bar"),
            attribute.style("width", width),
          ],
          [],
        ),
      ])
    }
    _ ->
      html.div([attribute.class("progress-bar-container")], [
        html.div([attribute.class("progress-bar progress-bar-indeterminate")], []),
      ])
  }

  html.div([attribute.class("modal-backdrop")], [
    html.div([attribute.class("modal")], [
      html.div([attribute.class("modal-header")], [
        html.h3([attribute.class("modal-title")], [
          html.text("Loading images..."),
        ]),
      ]),
      html.p([], [html.text(progress_text)]),
      progress_bar,
      html.div([attribute.class("modal-footer")], [
        html.button(
          [
            attribute.class("btn btn-secondary"),
            event.on_click(store.CancelPreload),
          ],
          [html.text("Cancel")],
        ),
      ]),
    ]),
  ])
}

fn render_confirm_modal(model: Model) -> Element(Msg) {
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
    router.StudyDetail(_), Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadRecords)
    router.StudyViewer(_), Some(models.User(is_superuser: True, ..)) ->
      effect.none()
    router.SeriesDetail(_), Some(models.User(is_superuser: True, ..)) ->
      effect.none()
    router.Records, _ -> dispatch_msg(store.LoadRecords)
    router.RecordDetail(id), Some(_) ->
      // Schema loading + slicer ping handled by page init
      dispatch_msg(store.LoadRecordDetail(id))
    router.Patients, Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadPatients)
    router.PatientDetail(id), Some(models.User(is_superuser: True, ..)) ->
      effect.batch([
        dispatch_msg(store.LoadPatientDetail(id)),
        dispatch_msg(store.LoadRecords),
      ])
    router.RecordNew, Some(models.User(is_superuser: True, ..)) ->
      effect.batch([
        dispatch_msg(store.LoadPatients),
        dispatch_msg(store.LoadRecordTypes),
        dispatch_msg(store.LoadUsers),
        dispatch_msg(store.LoadRecords),
      ])
    router.PatientNew, _ -> effect.none()
    router.Users, Some(models.User(is_superuser: True, ..)) ->
      dispatch_msg(store.LoadUsers)
    router.AdminDashboard, Some(_) ->
      effect.batch([
        dispatch_msg(store.LoadRecords),
        dispatch_msg(store.LoadUsers),
      ])
    router.AdminRecordTypes, Some(_) -> dispatch_msg(store.LoadRecordTypeStats)
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
