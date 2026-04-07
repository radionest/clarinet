// Record execution page — self-contained MVU module
import api/models.{type Record, type RecordType}
import api/records
import api/slicer
import api/types.{type ApiError, type RecordStatus, AuthError}
import config
import formosh/component as formosh_component
import gleam/dict
import gleam/dynamic.{type Dynamic}
import gleam/dynamic/decode
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/javascript/promise
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import plinth/javascript/global
import router
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}
import utils/logger
import utils/permissions
import utils/viewer

// --- Model ---

pub type Model {
  Model(
    record_id: String,
    record_load_status: LoadStatus,
    slicer_loading: Bool,
    slicer_available: Option(Bool),
    slicer_ping_timer: Option(global.TimerID),
    hydrated_schema: Option(String),
  )
}

// --- Msg ---

pub type Msg {
  // Record load tracker (parallel to shared.ReloadRecord which also drives
  // cache + auto-assign in cache.gleam — see init for details)
  RecordLoadProbe(Result(Record, ApiError))
  RetryLoad
  // Formosh form events
  FormSubmitSuccess
  FormSubmitError(String)
  // Record completion
  CompleteRecord
  CompleteRecordResult(Result(Record, ApiError))
  ResubmitRecord
  ResubmitRecordResult(Result(Record, ApiError))
  // Slicer operations
  OpenInSlicer
  SlicerOpenResult(Result(Dynamic, ApiError))
  SlicerValidate
  SlicerValidateResult(Result(Dynamic, ApiError))
  SlicerClearScene
  SlicerClearSceneResult(Result(Dynamic, ApiError))
  SlicerPing
  SlicerPingResult(Result(Dynamic, ApiError))
  SlicerPingTimerStarted(global.TimerID)
  // Schema hydration
  SchemaLoaded(Result(String, ApiError))
  // Navigation & actions
  NavigateBack
  Restart
  RestartResult(Result(Record, ApiError))
  RequestFail
  RequestPreload(viewer_url: String, study_uid: String)
}

// --- Init ---

pub fn init(record_id: String, _shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      record_id: record_id,
      record_load_status: load_status.Loading,
      slicer_loading: False,
      slicer_available: None,
      slicer_ping_timer: None,
      hydrated_schema: None,
    )

  // Start slicer ping timer + load hydrated schema
  let ping_eff = start_slicer_ping_timer()
  let schema_eff = {
    use dispatch <- effect.from
    records.get_hydrated_schema(record_id)
    |> promise.tap(fn(result) { dispatch(SchemaLoaded(result)) })
    Nil
  }
  // shared.ReloadRecord drives cache + auto-assign (cache.gleam owns that
  // logic). We additionally fire a local probe so the page can distinguish
  // a failed fetch from a still-loading state without leaking the toast as
  // the only failure signal. Browser HTTP cache typically dedupes the two
  // GETs.
  let probe_eff = load_record_probe_effect(record_id)

  #(
    model,
    effect.batch([ping_eff, schema_eff, probe_eff]),
    [shared.ReloadRecord(record_id)],
  )
}

fn load_record_probe_effect(record_id: String) -> Effect(Msg) {
  use dispatch <- effect.from
  records.get_record(record_id)
  |> promise.tap(fn(result) { dispatch(RecordLoadProbe(result)) })
  Nil
}

/// Cleanup slicer ping timer — called from main.gleam on route change
pub fn cleanup(model: Model) -> Effect(Msg) {
  case model.slicer_ping_timer {
    Some(timer_id) ->
      effect.from(fn(_dispatch) { global.clear_interval(timer_id) })
    None -> effect.none()
  }
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    // Record load probe (parallel to cache.gleam ReloadRecord flow)
    RecordLoadProbe(Ok(_)) ->
      #(
        Model(..model, record_load_status: load_status.Loaded),
        effect.none(),
        [],
      )

    RecordLoadProbe(Error(_)) ->
      #(
        Model(
          ..model,
          record_load_status: load_status.Failed("Failed to load record"),
        ),
        effect.none(),
        [],
      )

    RetryLoad ->
      #(
        Model(..model, record_load_status: load_status.Loading),
        load_record_probe_effect(model.record_id),
        [shared.ReloadRecord(model.record_id)],
      )

    // Schema hydration
    SchemaLoaded(Ok(schema_json)) ->
      #(Model(..model, hydrated_schema: Some(schema_json)), effect.none(), [])

    SchemaLoaded(Error(_)) ->
      // Silently fall back to static schema
      #(model, effect.none(), [])

    // Formosh form events
    FormSubmitSuccess -> {
      logger.info("form", "submit success: record_id=" <> model.record_id)
      let slicer_effect = case has_slicer_script(model.record_id, shared) {
        True -> {
          logger.info("slicer", "clearing scene after form submit")
          dispatch_local(SlicerClearScene)
        }
        False -> effect.none()
      }
      #(
        model,
        slicer_effect,
        [
          shared.ShowSuccess("Record data submitted successfully"),
          shared.ReloadRecord(model.record_id),
        ],
      )
    }

    FormSubmitError(error) ->
      #(model, effect.none(), [shared.ShowError(error)])

    // Record completion (no form)
    CompleteRecord -> {
      let eff = {
        use dispatch <- effect.from
        records.submit_record(model.record_id)
        |> promise.tap(fn(result) { dispatch(CompleteRecordResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    CompleteRecordResult(Ok(record)) -> {
      let slicer_eff = case has_slicer_script(model.record_id, shared) {
        True -> dispatch_local(SlicerClearScene)
        False -> effect.none()
      }
      #(model, slicer_eff, [
        shared.SetLoading(False),
        shared.CacheRecord(record),
        shared.ShowSuccess("Record completed successfully"),
        shared.ReloadRecord(model.record_id),
      ])
    }

    CompleteRecordResult(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to complete record"))

    // Re-submit finished record
    ResubmitRecord -> {
      let eff = {
        use dispatch <- effect.from
        records.resubmit_record(model.record_id)
        |> promise.tap(fn(result) { dispatch(ResubmitRecordResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    ResubmitRecordResult(Ok(record)) -> {
      let slicer_eff = case has_slicer_script(model.record_id, shared) {
        True -> dispatch_local(SlicerClearScene)
        False -> effect.none()
      }
      #(model, slicer_eff, [
        shared.SetLoading(False),
        shared.CacheRecord(record),
        shared.ShowSuccess("Record re-submitted successfully"),
        shared.ReloadRecord(model.record_id),
      ])
    }

    ResubmitRecordResult(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to re-submit record"))

    // Slicer operations
    OpenInSlicer -> {
      logger.info("slicer", "opening: record_id=" <> model.record_id)
      let eff = {
        use dispatch <- effect.from
        slicer.open_record(model.record_id)
        |> promise.tap(fn(result) { dispatch(SlicerOpenResult(result)) })
        Nil
      }
      #(Model(..model, slicer_loading: True), eff, [])
    }

    SlicerOpenResult(Ok(_)) -> {
      logger.info("slicer", "open completed")
      #(
        Model(..model, slicer_loading: False),
        effect.none(),
        [shared.ShowSuccess("Workspace opened in 3D Slicer")],
      )
    }

    SlicerOpenResult(Error(err)) -> {
      let error_msg = slicer_error_msg(err, "Failed to open record in Slicer")
      #(
        Model(..model, slicer_loading: False),
        effect.none(),
        [shared.ShowError(error_msg)],
      )
    }

    SlicerValidate -> {
      logger.info("slicer", "validating: record_id=" <> model.record_id)
      let eff = {
        use dispatch <- effect.from
        slicer.validate_record(model.record_id)
        |> promise.tap(fn(result) { dispatch(SlicerValidateResult(result)) })
        Nil
      }
      #(Model(..model, slicer_loading: True), eff, [])
    }

    SlicerValidateResult(Ok(_)) -> {
      logger.info("slicer", "validation completed")
      #(
        Model(..model, slicer_loading: False),
        dispatch_local(SlicerClearScene),
        [shared.ShowSuccess("Slicer validation completed")],
      )
    }

    SlicerValidateResult(Error(err)) -> {
      let error_msg = slicer_error_msg(err, "Slicer validation failed")
      #(
        Model(..model, slicer_loading: False),
        effect.none(),
        [shared.ShowError(error_msg)],
      )
    }

    SlicerClearScene -> {
      let eff = {
        use dispatch <- effect.from
        slicer.clear_scene()
        |> promise.tap(fn(result) { dispatch(SlicerClearSceneResult(result)) })
        Nil
      }
      #(model, eff, [])
    }

    SlicerClearSceneResult(_) ->
      // Silently ignore — data is already saved
      #(model, effect.none(), [])

    SlicerPing -> {
      // Skip pinging once we know the record has no slicer_script — also
      // tear down the interval timer so we don't keep dispatching no-ops.
      // While the record is still loading the cache lookup returns False,
      // so the very first ping (fired before ReloadRecord lands) still
      // goes out; subsequent ticks (10s apart) catch up.
      case record_definitely_has_no_slicer_script(model.record_id, shared) {
        True -> {
          logger.info(
            "slicer",
            "stopping ping timer: record has no slicer_script",
          )
          #(Model(..model, slicer_ping_timer: None), cleanup(model), [])
        }
        False -> {
          let eff = {
            use dispatch <- effect.from
            slicer.ping()
            |> promise.tap(fn(result) { dispatch(SlicerPingResult(result)) })
            Nil
          }
          #(model, eff, [])
        }
      }
    }

    SlicerPingResult(Ok(data)) -> {
      let ok_decoder = decode.at(["ok"], decode.bool)
      let available = case decode.run(data, ok_decoder) {
        Ok(True) -> True
        _ -> False
      }
      #(Model(..model, slicer_available: Some(available)), effect.none(), [])
    }

    SlicerPingResult(Error(_)) ->
      #(Model(..model, slicer_available: Some(False)), effect.none(), [])

    SlicerPingTimerStarted(timer_id) ->
      #(Model(..model, slicer_ping_timer: Some(timer_id)), effect.none(), [])

    // Navigation
    NavigateBack ->
      #(model, effect.none(), [shared.Navigate(router.Records)])

    // Restart
    Restart -> {
      let eff = {
        use dispatch <- effect.from
        records.restart_record(model.record_id)
        |> promise.tap(fn(result) { dispatch(RestartResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    RestartResult(Ok(record)) ->
      #(model, effect.none(), [
        shared.SetLoading(False),
        shared.CacheRecord(record),
        shared.ShowSuccess("Record restarted successfully"),
        shared.ReloadRecords,
      ])

    RestartResult(Error(err)) ->
      #(model, effect.none(), handle_error(err, "Failed to restart record"))

    RequestFail ->
      #(model, effect.none(), [shared.OpenFailPrompt(model.record_id)])

    RequestPreload(viewer_url, study_uid) ->
      #(model, effect.none(), [shared.StartPreload(viewer_url, study_uid)])
  }
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

fn has_slicer_script(record_id: String, shared: Shared) -> Bool {
  case dict.get(shared.cache.records, record_id) {
    Ok(models.Record(
      record_type: Some(models.RecordType(slicer_script: Some(_), ..)),
      ..,
    )) -> True
    _ -> False
  }
}

/// Returns True only when the record is in the cache AND its record_type
/// has no slicer_script (or no record_type at all). Returns False if the
/// record is not yet in the cache, so callers defer the decision until
/// the next tick.
fn record_definitely_has_no_slicer_script(
  record_id: String,
  shared: Shared,
) -> Bool {
  case dict.get(shared.cache.records, record_id) {
    Ok(models.Record(
      record_type: Some(models.RecordType(slicer_script: None, ..)),
      ..,
    )) -> True
    Ok(models.Record(record_type: None, ..)) -> True
    _ -> False
  }
}

fn slicer_error_msg(err: ApiError, fallback: String) -> String {
  case err {
    types.ServerError(502, _) ->
      "3D Slicer is not reachable. Is it running?"
    types.ServerError(_, msg) -> "Slicer error: " <> msg
    types.NetworkError(msg) -> "Network error: " <> msg
    _ -> fallback
  }
}

fn dispatch_local(msg: Msg) -> Effect(Msg) {
  use dispatch <- effect.from
  dispatch(msg)
}

fn start_slicer_ping_timer() -> Effect(Msg) {
  use dispatch <- effect.from
  // Immediate first ping
  dispatch(SlicerPing)
  // Set up periodic pings every 10 seconds
  let timer_id =
    global.set_interval(10_000, fn() { dispatch(SlicerPing) })
  dispatch(SlicerPingTimerStarted(timer_id))
}

// --- View ---

pub fn view(model: Model, shared: Shared) -> Element(Msg) {
  load_status.render(
    model.record_load_status,
    fn() { loading_view(model.record_id) },
    fn() {
      case dict.get(shared.cache.records, model.record_id) {
        Ok(record) -> render_record_execution(model, record, shared)
        Error(_) -> loading_view(model.record_id)
      }
    },
    fn(msg) { retry_error_view(msg) },
  )
}

fn render_record_execution(
  model: Model,
  record: Record,
  shared: Shared,
) -> Element(Msg) {
  html.div([attribute.class("record-execution-page")], [
    // Header
    html.div([attribute.class("page-header")], [
      html.h2([], [html.text("Record Execution")]),
      render_record_status(record.status),
    ]),
    // Record information
    html.div([attribute.class("record-info card")], [
      html.h3([], [
        html.text(
          option.map(record.record_type, fn(d) { d.label })
          |> option.flatten
          |> option.unwrap("Record"),
        ),
      ]),
      html.p([attribute.class("record-description")], [
        html.text(
          option.map(record.record_type, fn(d) { d.description })
          |> option.flatten
          |> option.unwrap("Complete the record form below"),
        ),
      ]),
      render_record_metadata(record),
      viewer.record_viewer_button(
        record.study_uid,
        record.series_uid,
        record.viewer_study_uids,
        record.viewer_series_uids,
        option.map(record.record_type, fn(rt) { rt.level }),
        "btn btn-primary",
        fn(url, study_uid) { RequestPreload(url, study_uid) },
      ),
    ]),
    // Slicer toolbar (only if record type has slicer_script)
    render_slicer_toolbar(model, record),
    // Dynamic form based on record type's data_schema
    html.div([attribute.class("record-form-container card")], [
      case record.record_type {
        Some(record_type) ->
          render_dynamic_form(model, record, record_type, shared.user)
        None -> error_view("Record type not found")
      },
    ]),
    // Action buttons
    html.div([attribute.class("page-actions")], [
      html.button(
        [
          attribute.class("btn btn-secondary"),
          event.on_click(NavigateBack),
        ],
        [html.text("Back to Records")],
      ),
      case permissions.can_fail_record(record, shared.user) {
        True ->
          html.button(
            [
              attribute.class("btn btn-danger"),
              event.on_click(RequestFail),
            ],
            [html.text("Fail")],
          )
        False -> element.none()
      },
      case permissions.can_restart_record(record, shared.user) {
        True ->
          html.button(
            [
              attribute.class("btn btn-warning"),
              event.on_click(Restart),
            ],
            [html.text("Restart")],
          )
        False -> element.none()
      },
    ]),
  ])
}

fn render_slicer_toolbar(
  model: Model,
  record: Record,
) -> Element(Msg) {
  let has_script = case record.record_type {
    Some(models.RecordType(slicer_script: Some(_), ..)) -> True
    _ -> False
  }

  case has_script {
    False -> element.none()
    True -> {
      let status_badge = case model.slicer_available {
        Some(True) ->
          html.span([attribute.class("badge badge-success")], [
            html.text("Connected"),
          ])
        Some(False) ->
          html.span([attribute.class("badge badge-danger")], [
            html.text("Unreachable"),
          ])
        None ->
          html.span([attribute.class("badge badge-pending")], [
            html.text("Checking..."),
          ])
      }

      let btn_disabled =
        model.slicer_loading || model.slicer_available != Some(True)

      html.div([attribute.class("slicer-toolbar card")], [
        html.div([attribute.class("slicer-toolbar-header")], [
          html.h4([], [html.text("3D Slicer")]),
          status_badge,
        ]),
        html.div([attribute.class("slicer-toolbar-actions")], [
          html.button(
            [
              attribute.class("btn btn-primary"),
              attribute.disabled(btn_disabled),
              event.on_click(OpenInSlicer),
            ],
            [
              case model.slicer_loading {
                True -> html.text("Opening...")
                False -> html.text("Open in Slicer")
              },
            ],
          ),
        ]),
      ])
    }
  }
}

fn render_dynamic_form(
  model: Model,
  record: Record,
  record_type: RecordType,
  user: Option(models.User),
) -> Element(Msg) {
  // Prefer hydrated schema over static schema
  let effective_schema = case model.hydrated_schema {
    Some(hydrated) -> Some(hydrated)
    None -> record_type.data_schema
  }

  case effective_schema {
    Some(schema_json) -> {
      let can_edit =
        permissions.can_fill_record(record, user)
        || permissions.can_edit_record(record, user)
      case can_edit {
        True -> render_editable_form(schema_json, model.record_id, record)
        False -> render_readonly_data(record)
      }
    }
    None -> {
      let can_complete = permissions.can_fill_record(record, user)
      let can_resubmit = permissions.can_edit_record(record, user)
      html.div([attribute.class("no-schema")], [
        case can_complete, can_resubmit {
          True, _ ->
            html.div([attribute.class("complete-record-actions")], [
              html.p([], [
                html.text("This record does not require form data."),
              ]),
              html.button(
                [
                  attribute.class("btn btn-success"),
                  event.on_click(CompleteRecord),
                ],
                [html.text("Complete Record")],
              ),
            ])
          _, True ->
            html.div([attribute.class("complete-record-actions")], [
              html.p([], [
                html.text("Record completed. Re-submit after changes."),
              ]),
              html.button(
                [
                  attribute.class("btn btn-success"),
                  event.on_click(ResubmitRecord),
                ],
                [html.text("Re-submit")],
              ),
            ])
          _, _ ->
            html.div([], [
              html.p([], [
                html.text("This record does not have a data form defined."),
              ]),
              case record.data {
                Some(data) -> render_raw_data(data)
                None -> html.text("No data submitted.")
              },
            ])
        },
      ])
    }
  }
}

fn render_editable_form(
  schema_json: String,
  record_id: String,
  record: Record,
) -> Element(Msg) {
  let submit_url = case record.record_type {
    Some(models.RecordType(slicer_result_validator: Some(_), ..)) ->
      config.base_path() <> "/api/records/" <> record_id <> "/submit"
    _ -> config.base_path() <> "/api/records/" <> record_id <> "/data"
  }
  let is_finished = record.status == types.Finished
  let method = case is_finished {
    True -> "PATCH"
    False -> "POST"
  }

  let base_attrs = [
    formosh_component.schema_string(schema_json),
    formosh_component.submit_url(submit_url),
    formosh_component.submit_method(method),
    event.on("formosh-submit", decode_form_submit()),
  ]

  let attrs = case record.data {
    Some(data) ->
      list.append(base_attrs, [formosh_component.initial_values_string(data)])
    None -> base_attrs
  }

  formosh_component.element(attrs)
}

fn decode_form_submit() -> decode.Decoder(Msg) {
  use status <- decode.then(decode.at(["detail", "status"], decode.string))

  case status {
    "success" -> decode.success(FormSubmitSuccess)
    _ -> {
      use error <- decode.then(
        decode.one_of(
          decode.at(["detail", "error"], decode.string),
          [decode.success("Submission failed")],
        ),
      )
      decode.success(FormSubmitError(error))
    }
  }
}

fn render_readonly_data(record: Record) -> Element(Msg) {
  case record.data {
    Some(data) -> render_raw_data(data)
    None ->
      html.div([attribute.class("no-data")], [
        html.p([], [html.text("No data submitted yet")]),
      ])
  }
}

fn render_record_status(status: RecordStatus) -> Element(Msg) {
  let #(class, text) = case status {
    types.Blocked -> #("badge-blocked", "Blocked")
    types.Pending -> #("badge-pending", "Pending")
    types.InWork -> #("badge-progress", "In Progress")
    types.Finished -> #("badge-success", "Completed")
    types.Failed -> #("badge-danger", "Failed")
    types.Paused -> #("badge-paused", "Paused")
  }

  html.span([attribute.class("badge " <> class)], [html.text(text)])
}

fn format_series_label(
  modality: option.Option(String),
  description: option.Option(String),
) -> String {
  case modality, description {
    Some(m), Some(d) -> m <> " - " <> d
    Some(m), None -> m
    None, Some(d) -> d
    None, None -> "-"
  }
}

fn render_record_metadata(record: Record) -> Element(Msg) {
  html.div([attribute.class("record-metadata")], [
    html.dl([], [
      html.dt([], [html.text("Patient:")]),
      html.dd([], [html.text(record.patient_id)]),
      case record.study {
        Some(study) ->
          element.fragment([
            html.dt([], [html.text("Study:")]),
            html.dd([], [
              html.text(
                option.unwrap(study.study_description, study.study_uid)
                <> " (" <> study.date <> ")",
              ),
            ]),
          ])
        None ->
          case record.study_uid {
            Some(uid) ->
              element.fragment([
                html.dt([], [html.text("Study:")]),
                html.dd([], [html.text(uid)]),
              ])
            None -> element.none()
          }
      },
      case record.series {
        Some(series) ->
          element.fragment([
            html.dt([], [html.text("Series:")]),
            html.dd([], [
              html.text(
                format_series_label(
                  series.modality,
                  series.series_description,
                )
                <> case series.instance_count {
                  Some(n) -> " (" <> int.to_string(n) <> " img)"
                  None -> ""
                },
              ),
            ]),
          ])
        None ->
          case record.series_uid {
            Some(uid) ->
              element.fragment([
                html.dt([], [html.text("Series:")]),
                html.dd([], [html.text(uid)]),
              ])
            None -> element.none()
          }
      },
      case record.created_at {
        Some(date) ->
          element.fragment([
            html.dt([], [html.text("Created:")]),
            html.dd([], [html.text(date)]),
          ])
        None -> element.none()
      },
      case record.user {
        Some(user) ->
          element.fragment([
            html.dt([], [html.text("Assigned to:")]),
            html.dd([], [html.text(user.email)]),
          ])
        None -> element.none()
      },
    ]),
  ])
}

fn render_raw_data(data: String) -> Element(Msg) {
  html.div([attribute.class("raw-data")], [
    html.h4([], [html.text("Record Data:")]),
    html.pre([attribute.class("json-display")], [
      html.code([], [html.text(data)]),
    ]),
  ])
}

fn loading_view(record_id: String) -> Element(Msg) {
  html.div([attribute.class("loading-container")], [
    html.div([attribute.class("spinner")], []),
    html.p([], [html.text("Loading record " <> record_id <> "...")]),
  ])
}

fn error_view(message: String) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
  ])
}

fn retry_error_view(message: String) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
    html.button(
      [attribute.class("btn btn-primary"), event.on_click(RetryLoad)],
      [html.text("Retry")],
    ),
  ])
}
