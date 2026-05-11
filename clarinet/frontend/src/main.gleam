// Main Lustre application
import api/auth
import api/info
import api/records
import api/types
import cache
import clarinet_frontend/i18n
import components/layout
import formosh/component as formosh_component
import gleam/bool
import gleam/javascript/promise
import gleam/list
import gleam/option.{None, Some}
import gleam/result
import gleam/string
import gleam/uri.{type Uri}
import utils/logger
import utils/permissions
import utils/storage as app_storage
import lustre
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import modem
import plinth/javascript/global
import plinth/javascript/storage
import plinth/browser/window
import preload
import pages/admin as admin_page
import pages/admin/reports as admin_reports_page
import pages/admin/workflow as admin_workflow_page
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

  // Restore locale from localStorage
  let saved_locale =
    storage.local()
    |> result.try(storage.get_item(_, "clarinet_locale"))
    |> result.map(i18n.locale_from_string)
    |> result.unwrap(i18n.En)
  let model = store.Model(..model, locale: saved_locale)

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
      // Cleanup current page (e.g. slicer ping timer) + stop preload
      let page_cleanup = cleanup_current_page(model)
      let preload_cleanup =
        effect.map(preload.stop_timer(model.preload), store.PreloadMsg)
      let cleanup_effect = effect.batch([page_cleanup, preload_cleanup])
      let was_preloading = preload.is_active(model.preload)
      // Force-close the create-record modal on navigation: it carries a live
      // record_new MVU instance with in-flight HTTP and is bound to the
      // page that opened it. ConfirmDelete / FailRecordPrompt are stateless
      // markers and are handled by the existing preload-only branch.
      let has_create_record_modal = case model.modal_content {
        store.CreateRecord(_) -> True
        _ -> False
      }
      let should_close_modal = was_preloading || has_create_record_modal
      let new_model =
        store.Model(
          ..store.set_route(model, route),
          preload: preload.init(),
          modal_open: case should_close_modal {
            True -> False
            False -> model.modal_open
          },
          modal_content: case should_close_modal {
            True -> store.NoModal
            False -> model.modal_content
          },
        )

      let redirect_to = fn(target: Route) {
        #(
          store.set_route(model, target),
          effect.batch([
            cleanup_effect,
            modem.push(router.route_to_path(target), option.None, option.None),
          ]),
        )
      }

      // Don't redirect while session check is in progress
      use <- bool.guard(model.checking_session, #(new_model, cleanup_effect))

      // Redirect to login if auth required but no user
      use <- bool.guard(
        router.requires_auth(route) && model.user == None,
        redirect_to(router.Login),
      )

      // Redirect from login/register if already authenticated
      let is_auth_page = route == router.Login || route == router.Register
      use <- bool.guard(
        is_auth_page && model.user != None,
        redirect_to(router.Home),
      )

      // Redirect non-admin user away from admin route
      let is_non_admin = case model.user {
        Some(user) -> !permissions.is_admin_user(user)
        None -> False
      }
      use <- bool.guard(
        router.requires_admin_role(route) && is_non_admin,
        redirect_to(router.Home),
      )

      // Initialize page model for modular pages
      let #(new_model, page_init_eff) = init_page_for_route(new_model, route)
      #(new_model, effect.batch([cleanup_effect, page_init_eff]))
    }

    store.Navigate(route) -> {
      logger.debug("router", "Navigate route: " <> string.inspect(route))
      let path = router.route_to_path(route)
      let query = router.route_to_query(route)
      logger.debug("router", "pushing path: " <> path)
      #(model, modem.push(path, query, option.None))
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
            _, _, _ -> {
              let needs_admin = router.requires_admin_role(route)
              let is_non_admin = case new_model.user {
                Some(user) -> !permissions.is_admin_user(user)
                None -> False
              }
              case needs_admin && is_non_admin {
                True -> #(
                  store.set_route(new_model, router.Home),
                  modem.push(
                    router.route_to_path(router.Home),
                    option.None,
                    option.None,
                  ),
                )
                False -> {
                  let #(new_model, page_init_eff) =
                    init_page_for_route(new_model, route)
                  #(new_model, page_init_eff)
                }
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
      let clear_storage = app_storage.clear_prefixed(app_storage.Local)
      let preload_cleanup =
        effect.map(preload.stop_timer(model.preload), store.PreloadMsg)
      #(
        store.reset_for_logout(model),
        effect.batch([logout_effect, clear_storage, preload_cleanup]),
      )
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

    store.NoOp -> #(model, effect.none())

    // Cache delegation (all data loading, caches, auto-assign)
    store.CacheMsg(cmsg) -> delegate_cache(model, cmsg)

    // Manual fail — update reason in modal
    store.UpdateFailReason(reason) -> {
      #(store.Model(..model, fail_reason: reason), effect.none())
    }

    // Manual fail — confirm and submit
    store.ConfirmFailRecord(record_id) -> {
      let reason = string.trim(model.fail_reason)
      let eff = {
        use dispatch <- effect.from
        records.fail_record(record_id, reason)
        |> promise.tap(fn(result) {
          dispatch(store.FailRecordResult(result))
        })
        Nil
      }
      #(
        store.Model(
          ..model,
          modal_open: False,
          modal_content: store.NoModal,
          fail_reason: "",
          loading: True,
        ),
        eff,
      )
    }

    store.FailRecordResult(Ok(record)) -> {
      let is_admin = case model.user {
        Some(user) -> permissions.is_admin_user(user)
        None -> False
      }
      let new_model =
        store.Model(..model, cache: cache.put_record(model.cache, record))
        |> store.set_loading(False)
        |> store.set_success("Record marked as failed")
      #(
        new_model,
        dispatch_msg(store.CacheMsg(cache.LoadRecords(is_admin))),
      )
    }

    store.FailRecordResult(Error(err)) ->
      handle_api_error(model, err, "Failed to mark record as failed")

    // Modal actions
    store.OpenModal(content) -> {
      #(
        store.Model(..model, modal_open: True, modal_content: content),
        effect.none(),
      )
    }

    store.CloseModal -> {
      #(
        store.Model(..model, modal_open: False, modal_content: store.NoModal, fail_reason: ""),
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
        store.ConfirmDelete("record", _id) -> #(
          close_model,
          dispatch_msg(store.RecordExecuteMsg(record_execute.Delete)),
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
          viewers: project_info.viewers,
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

    store.AdminReportsMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.AdminReportsPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { admin_reports_page.update(m, page_msg, s) },
        store.AdminReportsPage,
        store.AdminReportsMsg,
      )

    store.AdminWorkflowMsg(page_msg) ->
      delegate_page_update(
        model,
        fn(p) { case p { store.AdminWorkflowPage(m) -> Ok(m) _ -> Error(Nil) } },
        fn(m, s) { admin_workflow_page.update(m, page_msg, s) },
        store.AdminWorkflowPage,
        store.AdminWorkflowMsg,
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

    // Modal-hosted instance of record_new — same update fn, different
    // storage slot (modal_content) and msg wrapper.
    store.RecordNewModalMsg(page_msg) ->
      case model.modal_content {
        store.CreateRecord(pm) -> {
          let #(new_pm, eff, out_msgs) =
            record_new.update(pm, page_msg, build_shared(model))
          let model_with_pm =
            store.Model(..model, modal_content: store.CreateRecord(new_pm))
          let #(model_after, out_effs) = apply_out_msgs(model_with_pm, out_msgs)
          #(
            model_after,
            effect.batch([
              effect.map(eff, store.RecordNewModalMsg),
              out_effs,
            ]),
          )
        }
        _ -> #(model, effect.none())
      }

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

    // Locale
    store.SetLocale(new_locale) -> {
      let save_effect = effect.from(fn(_dispatch) {
        let _ = case storage.local() {
          Ok(ls) -> storage.set_item(ls, "clarinet_locale", i18n.locale_to_string(new_locale))
          Error(_) -> Error(Nil)
        }
        Nil
      })
      #(store.Model(..model, locale: new_locale), save_effect)
    }

    // Preload delegation
    store.PreloadMsg(pmsg) -> delegate_preload(model, pmsg)
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

// Helper: dispatch a message as an effect
fn dispatch_msg(msg: Msg) -> Effect(Msg) {
  use dispatch <- effect.from
  dispatch(msg)
}

fn delegate_cache(model: Model, cmsg: cache.Msg) -> #(Model, Effect(Msg)) {
  let #(cache_model, eff, out_msgs) = cache.update(model.cache, cmsg)
  let model = store.Model(..model, cache: cache_model)
  let #(model, out_effects) =
    list.fold(out_msgs, #(model, []), fn(acc, om) {
      let #(m, effs) = acc
      case om {
        cache.Loading(b) -> #(store.set_loading(m, b), effs)
        cache.ApiFailure(err, fallback) -> {
          let #(m2, e2) = handle_api_error(m, err, fallback)
          #(m2, [e2, ..effs])
        }
        cache.AutoAssign(rid, uid) -> {
          let assign_eff = {
            use dispatch <- effect.from
            records.assign_record_user(rid, uid)
            |> promise.tap(fn(r) {
              dispatch(store.CacheMsg(cache.AutoAssignResult(r)))
            })
            Nil
          }
          #(m, [assign_eff, ..effs])
        }
      }
    })
  // Reverse out_effects so they run in the order cache.update declared them.
  let ordered_effects = [
    effect.map(eff, store.CacheMsg),
    ..list.reverse(out_effects)
  ]
  #(model, effect.batch(ordered_effects))
}

fn delegate_preload(model: Model, pmsg: preload.Msg) -> #(Model, Effect(Msg)) {
  let was_active = preload.is_active(model.preload)
  let #(preload_model, eff, out_msgs) = preload.update(model.preload, pmsg)
  let now_active = preload.is_active(preload_model)
  // While preload is active it owns the modal layer; once it deactivates
  // (ready / error / cancel), we must clear modal_open or the generic
  // confirm modal would render on top of the page on its own.
  // Only touch modal_open when the preload state actually transitioned —
  // otherwise we would clobber a content-driven modal that has nothing
  // to do with preload.
  let modal_open = case was_active, now_active {
    _, True -> True
    True, False -> False
    False, False -> model.modal_open
  }
  let model = store.Model(
    ..model,
    preload: preload_model,
    modal_open: modal_open,
  )
  let out_effects =
    list.fold(out_msgs, [], fn(acc, out_msg) {
      case out_msg {
        preload.OpenViewer(url) -> [
          effect.from(fn(_dispatch) {
            let _ = window.open(url, "_blank", "")
            Nil
          }),
          ..acc
        ]
        preload.ShowError(msg) -> {
          [dispatch_msg(store.SetError(Some(msg))), ..acc]
        }
      }
    })
  #(model, effect.batch([effect.map(eff, store.PreloadMsg), ..out_effects]))
}

fn build_shared(model: Model) -> shared.Shared {
  shared.Shared(
    user: model.user,
    route: model.route,
    project_name: model.project_name,
    project_description: model.project_description,
    cache: model.cache,
    viewers: model.viewers,
    translate: i18n.translate(model.locale, _),
    locale: model.locale,
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

/// Generic page init helper — mirrors delegate_page_update pattern.
/// Call site binds `id`/`name` via hole syntax, e.g. `study_detail.init(id, _)`.
fn init_page(
  model: Model,
  page_init: fn(shared.Shared) -> #(pm, Effect(pmsg), List(OutMsg)),
  wrap_page: fn(pm) -> store.PageModel,
  wrap_msg: fn(pmsg) -> Msg,
) -> #(Model, Effect(Msg)) {
  let #(page_model, page_eff, out_msgs) = page_init(build_shared(model))
  let model = store.Model(..model, page: wrap_page(page_model))
  let #(model, out_effs) = apply_out_msgs(model, out_msgs)
  #(model, effect.batch([effect.map(page_eff, wrap_msg), out_effs]))
}

fn init_page_for_route(model: Model, route: Route) -> #(Model, Effect(Msg)) {
  case route {
    router.Home ->
      init_page(model, home.init, store.HomePage, store.HomeMsg)
    router.Login ->
      init_page(model, login.init, store.LoginPage, store.LoginMsg)
    router.Register ->
      init_page(model, register.init, store.RegisterPage, store.RegisterMsg)
    router.AdminDashboard(filters) ->
      init_page(model, admin_page.init(filters, _), store.AdminPage, store.AdminMsg)
    router.Patients(filters) ->
      init_page(model, patients_list.init(filters, _), store.PatientsListPage, store.PatientsListMsg)
    router.PatientDetail(id) ->
      init_page(model, patient_detail.init(id, _), store.PatientDetailPage, store.PatientDetailMsg)
    router.PatientNew ->
      init_page(model, patient_new.init, store.PatientNewPage, store.PatientNewMsg)
    router.Records(filters) ->
      init_page(model, records_list.init(filters, _), store.RecordsListPage, store.RecordsListMsg)
    router.RecordDetail(id) ->
      init_page(model, record_execute.init(id, _), store.RecordExecutePage, store.RecordExecuteMsg)
    router.RecordNew ->
      init_page(model, record_new.init, store.RecordNewPage, store.RecordNewMsg)
    router.Studies(filters) ->
      init_page(model, studies_list.init(filters, _), store.StudiesListPage, store.StudiesListMsg)
    router.StudyDetail(id) | router.StudyViewer(id) ->
      init_page(model, study_detail.init(id, _), store.StudyDetailPage, store.StudyDetailMsg)
    router.SeriesDetail(id) ->
      init_page(model, series_detail.init(id, _), store.SeriesDetailPage, store.SeriesDetailMsg)
    router.AdminRecordTypes ->
      init_page(model, record_types_list.init, store.RecordTypesListPage, store.RecordTypesListMsg)
    router.AdminRecordTypeDetail(name) ->
      init_page(model, record_type_detail.init(name, _), store.RecordTypeDetailPage, store.RecordTypeDetailMsg)
    router.AdminRecordTypeEdit(name) ->
      init_page(model, record_type_edit.init(name, _), store.RecordTypeEditPage, store.RecordTypeEditMsg)
    router.AdminReports ->
      init_page(model, admin_reports_page.init, store.AdminReportsPage, store.AdminReportsMsg)
    router.AdminWorkflow ->
      init_page(model, admin_workflow_page.init, store.AdminWorkflowPage, store.AdminWorkflowMsg)
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
          #(m, [modem.push(router.route_to_path(route), router.route_to_query(route), option.None), ..eff_list])
        shared.SetLoading(loading) ->
          #(store.set_loading(m, loading), eff_list)
        shared.CacheRecord(record) -> {
          let new_cache =
            cache.put_record(m.cache, record)
            |> cache.upsert_record_in_buckets(record)
          #(store.Model(..m, cache: new_cache), [
            dispatch_msg(store.CacheMsg(cache.InvalidateAllRecordBucketsMsg)),
            ..eff_list
          ])
        }
        shared.CacheStudy(study) ->
          #(store.Model(..m, cache: cache.put_study(m.cache, study)), eff_list)
        shared.CachePatient(patient) ->
          #(store.Model(..m, cache: cache.put_patient(m.cache, patient)), eff_list)
        shared.CacheRecordType(rt) ->
          #(store.Model(..m, cache: cache.put_record_type(m.cache, rt)), eff_list)
        shared.CacheSeries(s) ->
          #(store.Model(..m, cache: cache.put_series(m.cache, s)), eff_list)
        shared.FetchBucket(key) ->
          #(m, [dispatch_msg(store.CacheMsg(cache.FetchBucketMsg(key))), ..eff_list])
        shared.FetchMoreBucket(key) ->
          #(m, [dispatch_msg(store.CacheMsg(cache.FetchMoreMsg(key))), ..eff_list])
        shared.InvalidateBucket(key) ->
          #(m, [dispatch_msg(store.CacheMsg(cache.InvalidateBucketMsg(key))), ..eff_list])
        shared.InvalidateAllRecordBuckets ->
          #(m, [dispatch_msg(store.CacheMsg(cache.InvalidateAllRecordBucketsMsg)), ..eff_list])
        shared.ReloadStudies ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadStudies)), ..eff_list])
        shared.ReloadUsers ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadUsers)), ..eff_list])
        shared.ReloadPatients ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadPatients)), ..eff_list])
        shared.ReloadRecordTypes ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadRecordTypes)), ..eff_list])
        shared.ReloadRecordTypeStats ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadRecordTypeStats)), ..eff_list])
        shared.ReloadPatient(id) ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadPatientDetail(id))), ..eff_list])
        shared.ReloadRecord(id) ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadRecordDetail(id, m.user))), ..eff_list])
        shared.ReloadSeries(uid) ->
          #(m, [dispatch_msg(store.CacheMsg(cache.LoadSeriesDetail(uid))), ..eff_list])
        shared.OpenDeleteConfirm(resource, id) ->
          #(
            store.Model(..m, modal_open: True, modal_content: store.ConfirmDelete(resource, id)),
            eff_list,
          )
        shared.OpenFailPrompt(record_id) ->
          #(
            store.Model(
              ..m,
              modal_open: True,
              modal_content: store.FailRecordPrompt(record_id),
              fail_reason: "",
            ),
            eff_list,
          )
        shared.OpenCreateRecordModal(args) -> {
          // Spawn an embedded record_new instance prefilled with the source
          // page context. Its init OutMsgs (e.g. ReloadRecordTypes) are
          // applied recursively via apply_out_msgs.
          let #(modal_model, modal_eff, modal_out) =
            record_new.init_modal(args, build_shared(m))
          let #(m_after_inner, inner_eff) = apply_out_msgs(m, modal_out)
          let new_m =
            store.Model(
              ..m_after_inner,
              modal_open: True,
              modal_content: store.CreateRecord(modal_model),
            )
          #(
            new_m,
            [effect.map(modal_eff, store.RecordNewModalMsg), inner_eff, ..eff_list],
          )
        }
        shared.CloseRecordModal ->
          #(
            store.Model(..m, modal_open: False, modal_content: store.NoModal),
            eff_list,
          )
        shared.SetUser(user) ->
          #(store.set_user(m, user), eff_list)
        shared.Logout ->
          #(m, [dispatch_msg(store.Logout), ..eff_list])
        shared.StartPreload(viewer_url, study_uid) ->
          #(m, [dispatch_msg(store.PreloadMsg(preload.Start(viewer_url, study_uid))), ..eff_list])
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
      let clear_storage = app_storage.clear_prefixed(app_storage.Local)
      #(
        new_model,
        effect.batch([
          clear_storage,
          modem.push(router.route_to_path(router.Login), option.None, option.None),
        ]),
      )
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
  let shared = build_shared(model)

  let content = case model.page {
    store.HomePage(pm) ->
      element.map(home.view(pm, shared), store.HomeMsg)
    store.LoginPage(pm) ->
      element.map(login.view(pm, shared), store.LoginMsg)
    store.RegisterPage(pm) ->
      element.map(register.view(pm, shared), store.RegisterMsg)
    store.StudiesListPage(pm) ->
      element.map(studies_list.view(pm, shared), store.StudiesListMsg)
    store.StudyDetailPage(pm) ->
      element.map(study_detail.view(pm, shared), store.StudyDetailMsg)
    store.SeriesDetailPage(pm) ->
      element.map(series_detail.view(pm, shared), store.SeriesDetailMsg)
    store.RecordsListPage(pm) ->
      element.map(records_list.view(pm, shared), store.RecordsListMsg)
    store.RecordExecutePage(pm) ->
      element.map(record_execute.view(pm, shared), store.RecordExecuteMsg)
    store.RecordNewPage(pm) ->
      element.map(record_new.view(pm, shared), store.RecordNewMsg)
    store.PatientsListPage(pm) ->
      element.map(patients_list.view(pm, shared), store.PatientsListMsg)
    store.PatientDetailPage(pm) ->
      element.map(patient_detail.view(pm, shared), store.PatientDetailMsg)
    store.PatientNewPage(pm) ->
      element.map(patient_new.view(pm, shared), store.PatientNewMsg)
    store.AdminPage(pm) ->
      element.map(admin_page.view(pm, shared), store.AdminMsg)
    store.RecordTypesListPage(pm) ->
      element.map(record_types_list.view(pm, shared), store.RecordTypesListMsg)
    store.RecordTypeDetailPage(pm) ->
      element.map(record_type_detail.view(pm, shared), store.RecordTypeDetailMsg)
    store.RecordTypeEditPage(pm) ->
      element.map(record_type_edit.view(pm, shared), store.RecordTypeEditMsg)
    store.AdminReportsPage(pm) ->
      element.map(admin_reports_page.view(pm, shared), store.AdminReportsMsg)
    store.AdminWorkflowPage(pm) ->
      element.map(admin_workflow_page.view(pm, shared), store.AdminWorkflowMsg)
    store.NoPage -> render_route_placeholder(model.route)
  }

  let page = case model.route {
    router.Login | router.Register -> content
    _ -> layout.view(model, content)
  }

  case preload.is_active(model.preload) {
    True ->
      case model.preload.progress {
        Some(state) ->
          html.div([], [
            page,
            element.map(preload.view_modal(state), store.PreloadMsg),
          ])
        None -> page
      }
    False ->
      case model.modal_open {
        True -> html.div([], [page, render_modal(model)])
        False -> page
      }
  }
}

fn loading_placeholder() -> Element(Msg) {
  html.div([attribute.class("loading")], [
    html.p([], [html.text("Loading...")]),
  ])
}

/// Rendered when no `PageModel` is set yet for the current route.
/// `NotFound` lives here because it doesn't need its own MVU module.
/// Other routes hit this only as a transient state during init.
fn render_route_placeholder(route: Route) -> Element(Msg) {
  case route {
    router.NotFound ->
      html.div([attribute.class("not-found")], [
        html.h1([], [html.text("404")]),
        html.p([], [html.text("Page not found")]),
      ])
    _ -> loading_placeholder()
  }
}

fn render_modal(model: Model) -> Element(Msg) {
  case model.modal_content {
    store.FailRecordPrompt(record_id) -> render_fail_modal(model, record_id)
    store.CreateRecord(pm) -> render_create_record_modal(model, pm)
    _ -> render_confirm_modal(model)
  }
}

fn render_create_record_modal(
  model: Model,
  pm: record_new.Model,
) -> Element(Msg) {
  let shared = build_shared(model)
  html.div(
    [
      attribute.class("modal-backdrop"),
      // Click on the dark backdrop closes the modal. Clicks inside the
      // `.modal` element below stop propagation via the `NoOp` handler so
      // they don't bubble up to this listener.
      event.on_click(store.CloseModal),
    ],
    [
      html.div(
        [
          attribute.class("modal"),
          event.on_click(store.NoOp) |> event.stop_propagation,
        ],
        [
          html.div([attribute.class("modal-header")], [
            html.h3([attribute.class("modal-title")], [html.text("New Record")]),
          ]),
          html.div(
            [attribute.class("modal-body")],
            [element.map(record_new.view(pm, shared), store.RecordNewModalMsg)],
          ),
        ],
      ),
    ],
  )
}

fn render_fail_modal(model: Model, record_id: String) -> Element(Msg) {
  let is_empty = string.trim(model.fail_reason) == ""
  html.div([attribute.class("modal-backdrop")], [
    html.div([attribute.class("modal")], [
      html.div([attribute.class("modal-header")], [
        html.h3([attribute.class("modal-title")], [html.text("Mark as Failed")]),
      ]),
      html.div([attribute.class("form-group")], [
        html.label([], [html.text("Reason:")]),
        html.textarea(
          [
            attribute.class("form-control"),
            attribute.attribute("rows", "3"),
            attribute.value(model.fail_reason),
            event.on_input(store.UpdateFailReason),
            attribute.placeholder("Describe why this record is being failed..."),
          ],
          "",
        ),
      ]),
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
            attribute.disabled(is_empty),
            event.on_click(store.ConfirmFailRecord(record_id)),
          ],
          [html.text("Fail")],
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
    store.ConfirmDelete("record", id) -> #(
      "Delete Record",
      "Are you sure you want to delete record #"
        <> id
        <> "? This will cascade-delete all child records and their OUTPUT files. This action cannot be undone.",
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
