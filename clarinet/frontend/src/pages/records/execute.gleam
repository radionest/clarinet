// Record execution page — self-contained MVU module
import api/models.{type Record, type RecordType}
import api/records
import api/slicer
import api/types.{type ApiError, AuthError}
import api/workflow as wf_api
import api/workflow_models.{
  type ActionPreview, type DryRunResponse, type FireResponse,
  type TriggerKindRequest, type WorkflowGraph, type WorkflowNode,
  CallFunctionNode, DataUpdateTrigger, EntityNode, FileChangeTrigger, FileNode,
  PipelineNode, PipelineStepNode, RecordTypeNode, StatusTrigger,
  TriggerOnDataUpdate, TriggerOnFileChange, TriggerOnStatus,
}
import clarinet_frontend/i18n
import components/status_badge
import components/workflow_graph as wf_renderer
import config
import formosh/component as formosh_component
import gleam/bool
import gleam/dict
import gleam/dynamic.{type Dynamic}
import gleam/dynamic/decode
import gleam/int
import gleam/javascript/promise
import gleam/json
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/set.{type Set}
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
import utils/status as status_utils
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
    // Admin workflow section (visible only to admins)
    workflow_graph: Option(WorkflowGraph),
    workflow_load_status: LoadStatus,
    workflow_service_disabled: Bool,
    workflow_view: wf_renderer.ViewTransform,
    workflow_expanded: Set(String),
    workflow_selected_node: Option(String),
    workflow_selected_edge: Option(String),
    plan_state: PlanState,
    trigger_picker: TriggerPickerState,
    /// Generation counter — bumped on every workflow_load_effect dispatch
    /// so late responses from rapid TogglePipeline clicks are dropped.
    workflow_request_id: Int,
  )
}

/// What `Fire` would dispatch — captured at `dry_run` time so the digest
/// from the response matches the same parameters when the admin confirms.
/// `node_id` is the record_type node the trigger was fired from; the panel
/// uses it for the "Dry-run: <node label>" header and the Re-run button.
pub type PendingTrigger {
  PendingTrigger(
    node_id: String,
    trigger_kind: TriggerKindRequest,
    status_override: Option(String),
  )
}

/// Trigger picker shown when an admin clicks a `record_type` node. Lets them
/// pick which `trigger_kind` to fire (and, for status, which status to pretend
/// the record has). Closed after the admin clicks Plan (→ PlanLoading) or
/// Cancel.
pub type TriggerPickerState {
  NoPicker
  PickerOpen(
    node_id: String,
    kind: TriggerKindRequest,
    status_override: Option(String),
  )
}

/// Dry-run / fire state machine. Replaces a 4-field tuple
/// (pending_plan, pending_plan_status, pending_trigger, fire_in_flight)
/// to keep update/view branches exhaustive and prevent invalid
/// combinations (e.g. fire_in_flight=True with pending_plan=None).
///
/// `PlanFailed` carries the trigger that failed so the panel can offer a
/// "Re-run dry-run" button without forcing the admin to re-click the edge.
pub type PlanState {
  NoPlan
  PlanLoading(trigger: PendingTrigger)
  PlanReady(trigger: PendingTrigger, plan: DryRunResponse)
  PlanFiring(trigger: PendingTrigger, plan: DryRunResponse)
  PlanFailed(trigger: PendingTrigger, message: String)
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
  // Admin delete
  RequestDelete
  Delete
  DeleteResult(Result(Nil, ApiError))
  // Admin workflow section
  WorkflowGraphLoaded(request_id: Int, result: Result(WorkflowGraph, ApiError))
  WorkflowRetryLoad
  WorkflowPanZoom(wf_renderer.ViewTransform)
  WorkflowTogglePipeline(String)
  WorkflowNodeClicked(String)
  WorkflowEdgeClicked(String)
  WorkflowClearSelection
  WorkflowRecordTypeNodeClicked(String)
  PickerKindChanged(TriggerKindRequest)
  PickerStatusOverrideChanged(Option(String))
  PickerPlanClicked
  PickerCancelled
  DryRunReceived(Result(DryRunResponse, ApiError))
  RetryDryRun(PendingTrigger)
  ConfirmFireClicked
  FireResultReceived(Result(FireResponse, ApiError))
  DismissPlan
}

// --- Init ---

pub fn init(
  record_id: String,
  shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  let model =
    Model(
      record_id: record_id,
      record_load_status: load_status.Loading,
      slicer_loading: False,
      slicer_available: None,
      slicer_ping_timer: None,
      hydrated_schema: None,
      workflow_graph: None,
      workflow_load_status: load_status.Loading,
      workflow_service_disabled: False,
      workflow_view: wf_renderer.identity(),
      workflow_expanded: set.new(),
      workflow_selected_node: None,
      workflow_selected_edge: None,
      plan_state: NoPlan,
      trigger_picker: NoPicker,
      workflow_request_id: 1,
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
  let workflow_eff =
    workflow_load_effect_for_admin(shared, record_id, set.new(), 1)

  #(model, effect.batch([ping_eff, schema_eff, probe_eff, workflow_eff]), [
    shared.ReloadRecord(record_id),
  ])
}

/// Load the instance-mode workflow graph only when the current user is an
/// admin. Non-admins never see the section, so we don't waste a request
/// (the endpoint would 403 anyway). `request_id` lets late responses from
/// rapid expand/collapse clicks be ignored on arrival.
fn workflow_load_effect_for_admin(
  shared: Shared,
  record_id: String,
  expanded: Set(String),
  request_id: Int,
) -> Effect(Msg) {
  case is_admin_user(shared), int.parse(record_id) {
    True, Ok(rid) -> workflow_load_effect(rid, expanded, request_id)
    _, _ -> effect.none()
  }
}

fn workflow_load_effect(
  record_id: Int,
  expanded: Set(String),
  request_id: Int,
) -> Effect(Msg) {
  use dispatch <- effect.from
  wf_api.get_graph(Some(record_id), set.to_list(expanded), wf_api.Instance)
  |> promise.tap(fn(result) {
    dispatch(WorkflowGraphLoaded(request_id, result))
  })
  Nil
}

fn is_admin_user(shared: Shared) -> Bool {
  case shared.user {
    Some(u) -> permissions.is_admin_user(u)
    None -> False
  }
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
    RecordLoadProbe(Ok(_)) -> #(
      Model(..model, record_load_status: load_status.Loaded),
      effect.none(),
      [],
    )

    RecordLoadProbe(Error(_)) -> #(
      Model(
        ..model,
        record_load_status: load_status.Failed("Failed to load record"),
      ),
      effect.none(),
      [],
    )

    RetryLoad -> #(
      Model(..model, record_load_status: load_status.Loading),
      load_record_probe_effect(model.record_id),
      [shared.ReloadRecord(model.record_id)],
    )

    // Schema hydration
    SchemaLoaded(Ok(schema_json)) -> #(
      Model(..model, hydrated_schema: Some(schema_json)),
      effect.none(),
      [],
    )

    SchemaLoaded(Error(_)) ->
      // Silently fall back to static schema
      #(model, effect.none(), [])

    // Formosh form events
    FormSubmitSuccess -> {
      logger.info("form", "submit success: record_id=" <> model.record_id)
      let slicer_effect = case slicer_script_status(model.record_id, shared) {
        Some(True) -> {
          logger.info("slicer", "clearing scene after form submit")
          dispatch_local(SlicerClearScene)
        }
        _ -> effect.none()
      }
      #(model, slicer_effect, [
        shared.ShowSuccess("Record data submitted successfully"),
        shared.ReloadRecord(model.record_id),
      ])
    }

    FormSubmitError(error) -> #(model, effect.none(), [shared.ShowError(error)])

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
      let slicer_eff = case slicer_script_status(model.record_id, shared) {
        Some(True) -> dispatch_local(SlicerClearScene)
        _ -> effect.none()
      }
      #(model, slicer_eff, [
        shared.SetLoading(False),
        shared.CacheRecord(record),
        shared.ShowSuccess("Record completed successfully"),
        shared.ReloadRecord(model.record_id),
      ])
    }

    CompleteRecordResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to complete record"),
    )

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
      let slicer_eff = case slicer_script_status(model.record_id, shared) {
        Some(True) -> dispatch_local(SlicerClearScene)
        _ -> effect.none()
      }
      #(model, slicer_eff, [
        shared.SetLoading(False),
        shared.CacheRecord(record),
        shared.ShowSuccess("Record re-submitted successfully"),
        shared.ReloadRecord(model.record_id),
      ])
    }

    ResubmitRecordResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to re-submit record"),
    )

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
      #(Model(..model, slicer_loading: False), effect.none(), [
        shared.ShowSuccess("Workspace opened in 3D Slicer"),
      ])
    }

    SlicerOpenResult(Error(err)) -> {
      let error_msg = slicer_error_msg(err, "Failed to open record in Slicer")
      #(Model(..model, slicer_loading: False), effect.none(), [
        shared.ShowError(error_msg),
      ])
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
      #(Model(..model, slicer_loading: False), effect.none(), [
        shared.ShowError(error_msg),
      ])
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
      // While the record is still loading the cache lookup returns None,
      // so the very first ping (fired before ReloadRecord lands) still
      // goes out; subsequent ticks (10s apart) catch up.
      case slicer_script_status(model.record_id, shared) {
        Some(False) -> {
          logger.debug(
            "slicer",
            "stopping ping timer: record has no slicer_script",
          )
          #(Model(..model, slicer_ping_timer: None), cleanup(model), [])
        }
        _ -> {
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

    SlicerPingResult(Error(err)) ->
      case err {
        AuthError(_) -> #(
          model,
          effect.none(),
          handle_error(err, "Slicer ping failed"),
        )
        _ -> #(Model(..model, slicer_available: Some(False)), effect.none(), [])
      }

    SlicerPingTimerStarted(timer_id) -> #(
      Model(..model, slicer_ping_timer: Some(timer_id)),
      effect.none(),
      [],
    )

    // Navigation
    NavigateBack -> #(model, effect.none(), [
      shared.Navigate(router.Records(dict.new())),
    ])

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

    RestartResult(Ok(record)) -> #(model, effect.none(), [
      shared.SetLoading(False),
      shared.CacheRecord(record),
      shared.ShowSuccess("Record restarted successfully"),
      shared.InvalidateAllRecordBuckets,
    ])

    RestartResult(Error(err)) -> #(
      model,
      effect.none(),
      handle_error(err, "Failed to restart record"),
    )

    RequestFail -> #(model, effect.none(), [
      shared.OpenFailPrompt(model.record_id),
    ])

    RequestPreload(viewer_url, study_uid) -> #(model, effect.none(), [
      shared.StartPreload(viewer_url, study_uid),
    ])

    // Admin delete: open confirm modal first
    RequestDelete -> #(model, effect.none(), [
      shared.OpenDeleteConfirm("record", model.record_id),
    ])

    Delete -> {
      let eff = {
        use dispatch <- effect.from
        records.delete_record(model.record_id)
        |> promise.tap(fn(result) { dispatch(DeleteResult(result)) })
        Nil
      }
      #(model, eff, [shared.SetLoading(True)])
    }

    DeleteResult(Ok(_)) -> #(model, effect.none(), [
      shared.SetLoading(False),
      shared.InvalidateAllRecordBuckets,
      shared.ShowSuccess("Record deleted successfully"),
      shared.Navigate(router.Records(dict.new())),
    ])

    DeleteResult(Error(err)) -> {
      let msg = case err {
        types.StructuredError(_, _, _) ->
          "Cannot delete: subtree contains records currently in work"
        types.ServerError(409, _) ->
          "Cannot delete: subtree contains records currently in work"
        _ -> "Failed to delete record"
      }
      #(model, effect.none(), handle_error(err, msg))
    }

    // --- Workflow section (admin only) ---
    WorkflowGraphLoaded(id, _) if id != model.workflow_request_id ->
      // Stale response — superseded by a later TogglePipeline/Retry.
      #(model, effect.none(), [])

    WorkflowGraphLoaded(_, Ok(graph)) -> #(
      Model(
        ..model,
        workflow_graph: Some(graph),
        workflow_load_status: load_status.Loaded,
        workflow_service_disabled: False,
      ),
      effect.none(),
      [],
    )

    WorkflowGraphLoaded(_, Error(err)) -> {
      let #(load_state, disabled) = wf_api.classify_load_error(err)
      let out = case err {
        AuthError(_) -> [shared.Logout]
        _ -> []
      }
      #(
        Model(
          ..model,
          workflow_load_status: load_state,
          workflow_service_disabled: disabled,
        ),
        effect.none(),
        out,
      )
    }

    WorkflowRetryLoad -> {
      let next_id = model.workflow_request_id + 1
      #(
        Model(
          ..model,
          workflow_load_status: load_status.Loading,
          workflow_service_disabled: False,
          workflow_request_id: next_id,
        ),
        workflow_load_effect_for_admin(
          shared,
          model.record_id,
          model.workflow_expanded,
          next_id,
        ),
        [],
      )
    }

    WorkflowPanZoom(v) -> #(Model(..model, workflow_view: v), effect.none(), [])

    WorkflowTogglePipeline(name) -> {
      let new_expanded = case set.contains(model.workflow_expanded, name) {
        True -> set.delete(model.workflow_expanded, name)
        False -> set.insert(model.workflow_expanded, name)
      }
      let next_id = model.workflow_request_id + 1
      #(
        Model(
          ..model,
          workflow_expanded: new_expanded,
          workflow_load_status: load_status.Loading,
          workflow_request_id: next_id,
        ),
        workflow_load_effect_for_admin(
          shared,
          model.record_id,
          new_expanded,
          next_id,
        ),
        [],
      )
    }

    WorkflowNodeClicked(node_id) -> {
      let select_model =
        Model(
          ..model,
          workflow_selected_node: Some(node_id),
          workflow_selected_edge: None,
        )
      // Dispatch per node.kind: RecordType opens the trigger picker; Pipeline
      // toggles expand/collapse (legacy behaviour); everything else just
      // selects (metadata is shown in node_panel).
      let eff = case
        model.workflow_graph
        |> option.then(fn(g) {
          list.find(g.nodes, fn(n) { n.id == node_id })
          |> option.from_result
        })
      {
        Some(node) ->
          case node.kind {
            RecordTypeNode ->
              dispatch_local(WorkflowRecordTypeNodeClicked(node.id))
            PipelineNode ->
              case workflow_models.pipeline_name_from_id(node.id) {
                Some(name) -> dispatch_local(WorkflowTogglePipeline(name))
                None -> effect.none()
              }
            // Informational nodes — clicking only selects (no side effect).
            // Exhaustive match so adding a new NodeKind is a compile error.
            EntityNode | FileNode | PipelineStepNode | CallFunctionNode ->
              effect.none()
          }
        None -> effect.none()
      }
      #(select_model, eff, [])
    }

    WorkflowEdgeClicked(edge_id) ->
      // Edge clicks no longer trigger dry-run — they only select the edge so
      // edge_panel can show its metadata. dry-run/fire is initiated from
      // record_type nodes through the trigger picker.
      #(
        Model(
          ..model,
          workflow_selected_edge: Some(edge_id),
          workflow_selected_node: None,
        ),
        effect.none(),
        [],
      )

    WorkflowRecordTypeNodeClicked(node_id) ->
      case model.workflow_graph {
        Some(graph) -> {
          let kinds = available_kinds_from_node(graph, node_id)
          case default_trigger_kind(kinds) {
            Some(kind) -> {
              let status_override = case kind {
                StatusTrigger ->
                  default_status_override_for_node(graph, node_id)
                _ -> None
              }
              #(
                Model(
                  ..model,
                  trigger_picker: PickerOpen(node_id, kind, status_override),
                ),
                effect.none(),
                [],
              )
            }
            // No fireable outgoing edges — leave the node merely selected.
            None -> #(model, effect.none(), [])
          }
        }
        None -> #(model, effect.none(), [])
      }

    PickerKindChanged(kind) ->
      case model.trigger_picker {
        PickerOpen(node_id, _, prev_override) -> {
          // Switching to StatusTrigger re-seeds the override from the graph
          // (so the dropdown starts on a sensible value); switching away
          // drops the override since the dropdown is hidden anyway.
          let status_override = case kind {
            StatusTrigger ->
              case prev_override {
                Some(_) -> prev_override
                None ->
                  case model.workflow_graph {
                    Some(g) -> default_status_override_for_node(g, node_id)
                    None -> None
                  }
              }
            _ -> None
          }
          #(
            Model(
              ..model,
              trigger_picker: PickerOpen(node_id, kind, status_override),
            ),
            effect.none(),
            [],
          )
        }
        NoPicker -> #(model, effect.none(), [])
      }

    PickerStatusOverrideChanged(value) ->
      case model.trigger_picker {
        PickerOpen(node_id, kind, _) -> #(
          Model(..model, trigger_picker: PickerOpen(node_id, kind, value)),
          effect.none(),
          [],
        )
        NoPicker -> #(model, effect.none(), [])
      }

    PickerPlanClicked ->
      case model.trigger_picker, int.parse(model.record_id) {
        PickerOpen(node_id, kind, status_override), Ok(rid) -> {
          let pending =
            PendingTrigger(
              node_id: node_id,
              trigger_kind: kind,
              status_override: status_override,
            )
          #(
            Model(
              ..model,
              plan_state: PlanLoading(pending),
              trigger_picker: NoPicker,
            ),
            dry_run_effect(rid, pending),
            [],
          )
        }
        _, _ -> #(model, effect.none(), [])
      }

    PickerCancelled -> #(
      Model(..model, trigger_picker: NoPicker),
      effect.none(),
      [],
    )

    WorkflowClearSelection -> #(
      Model(
        ..model,
        workflow_selected_node: None,
        workflow_selected_edge: None,
        plan_state: NoPlan,
        trigger_picker: NoPicker,
      ),
      effect.none(),
      [],
    )

    DryRunReceived(Ok(resp)) ->
      // Late-arriving response — only accept while we still expect this plan.
      // (DismissPlan / WorkflowClearSelection / a new edge click would have
      // moved plan_state away from PlanLoading.)
      case model.plan_state {
        PlanLoading(t) -> #(
          Model(..model, plan_state: PlanReady(t, resp)),
          effect.none(),
          [],
        )
        _ -> #(model, effect.none(), [])
      }

    DryRunReceived(Error(err)) -> {
      let msg = error_detail(err, "Dry-run failed")
      case model.plan_state {
        PlanLoading(t) -> #(
          Model(..model, plan_state: PlanFailed(t, msg)),
          effect.none(),
          handle_workflow_error(err, msg),
        )
        _ -> #(model, effect.none(), [])
      }
    }

    RetryDryRun(pending) ->
      case int.parse(model.record_id) {
        Ok(rid) -> #(
          Model(..model, plan_state: PlanLoading(pending)),
          dry_run_effect(rid, pending),
          [],
        )
        Error(_) -> #(model, effect.none(), [])
      }

    ConfirmFireClicked -> {
      case model.plan_state, int.parse(model.record_id) {
        PlanReady(t, plan), Ok(rid) -> #(
          Model(..model, plan_state: PlanFiring(t, plan)),
          fire_effect(rid, t, plan.digest),
          [],
        )
        _, _ -> #(model, effect.none(), [])
      }
    }

    FireResultReceived(Ok(_)) -> {
      case model.plan_state {
        PlanFiring(_, _) -> {
          let next_id = model.workflow_request_id + 1
          #(
            Model(
              ..model,
              plan_state: NoPlan,
              workflow_load_status: load_status.Loading,
              workflow_request_id: next_id,
            ),
            workflow_load_effect_for_admin(
              shared,
              model.record_id,
              model.workflow_expanded,
              next_id,
            ),
            [
              shared.ShowSuccess("Workflow trigger fired."),
              shared.ReloadRecord(model.record_id),
            ],
          )
        }
        _ -> #(model, effect.none(), [])
      }
    }

    FireResultReceived(Error(err)) -> {
      // Drop the plan so the admin is forced to re-run dry-run (handles both
      // WORKFLOW_PLAN_CHANGED and WORKFLOW_DIGEST_ALREADY_USED; backend
      // `detail` string already says which one and what to do next).
      let msg = error_detail(err, "Fire failed")
      case model.plan_state {
        PlanFiring(t, _) -> #(
          Model(..model, plan_state: PlanFailed(t, msg)),
          effect.none(),
          handle_workflow_error(err, msg),
        )
        _ -> #(model, effect.none(), [])
      }
    }

    DismissPlan -> #(Model(..model, plan_state: NoPlan), effect.none(), [])
  }
}

/// Workflow operations (graph load, dry-run, fire) keep their loading state
/// in page-local `LoadStatus` fields, so we MUST NOT emit `shared.SetLoading`
/// — that would interact with the global spinner driven by record-level
/// mutations like `CompleteRecord` and clobber an unrelated in-flight op.
fn handle_workflow_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.ShowError(fallback_msg)]
  }
}

fn error_detail(err: ApiError, fallback: String) -> String {
  case err {
    types.StructuredError(_, msg, _) -> msg
    types.ServerError(_, msg) -> msg
    types.NetworkError(msg) -> msg
    _ -> fallback
  }
}

fn map_trigger_to_request(
  kind: workflow_models.TriggerKind,
) -> Option(TriggerKindRequest) {
  case kind {
    TriggerOnStatus -> Some(StatusTrigger)
    TriggerOnDataUpdate -> Some(DataUpdateTrigger)
    TriggerOnFileChange -> Some(FileChangeTrigger)
    _ -> None
  }
}

/// Outgoing fireable trigger kinds from a node, deduplicated. Used to render
/// the radio group in the trigger picker and to decide whether opening the
/// picker is meaningful at all (empty list → not a fireable record_type).
/// Public so `test/execute_picker_test.gleam` can exercise it.
pub fn available_kinds_from_node(
  graph: WorkflowGraph,
  node_id: String,
) -> List(TriggerKindRequest) {
  graph.edges
  |> list.filter(fn(e) { e.from_node == node_id })
  |> list.filter_map(fn(e) {
    case map_trigger_to_request(e.trigger_kind) {
      Some(k) -> Ok(k)
      None -> Error(Nil)
    }
  })
  |> list.unique
}

/// Preferred initial selection in the picker — Status (commonest case) >
/// DataUpdate > FileChange. Returns `None` only when there are no fireable
/// kinds at all, in which case the picker should not open.
pub fn default_trigger_kind(
  kinds: List(TriggerKindRequest),
) -> Option(TriggerKindRequest) {
  case
    list.contains(kinds, StatusTrigger),
    list.contains(kinds, DataUpdateTrigger),
    list.contains(kinds, FileChangeTrigger)
  {
    True, _, _ -> Some(StatusTrigger)
    _, True, _ -> Some(DataUpdateTrigger)
    _, _, True -> Some(FileChangeTrigger)
    _, _, _ -> None
  }
}

/// Pre-fill for the StatusTrigger dropdown — `trigger_value` of the first
/// outgoing `TriggerOnStatus` edge from `node_id`. Mirrors PR 1's edge-click
/// UX so opening the picker on a status-driven node lands on a meaningful
/// status by default. Wildcard rules (`trigger_value=None`) yield `None`.
pub fn default_status_override_for_node(
  graph: WorkflowGraph,
  node_id: String,
) -> Option(String) {
  graph.edges
  |> list.filter(fn(e) {
    e.from_node == node_id && e.trigger_kind == TriggerOnStatus
  })
  |> list.first
  |> option.from_result
  |> option.then(fn(e) { e.trigger_value })
}

fn dry_run_effect(record_id: Int, pending: PendingTrigger) -> Effect(Msg) {
  use dispatch <- effect.from
  wf_api.dry_run(record_id, pending.trigger_kind, pending.status_override)
  |> promise.tap(fn(result) { dispatch(DryRunReceived(result)) })
  Nil
}

fn fire_effect(
  record_id: Int,
  pending: PendingTrigger,
  digest: String,
) -> Effect(Msg) {
  use dispatch <- effect.from
  wf_api.fire(record_id, pending.trigger_kind, pending.status_override, digest)
  |> promise.tap(fn(result) { dispatch(FireResultReceived(result)) })
  Nil
}

// --- Helpers ---

fn handle_error(err: ApiError, fallback_msg: String) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> [shared.SetLoading(False), shared.ShowError(fallback_msg)]
  }
}

/// Tri-state slicer_script presence:
/// - `Some(True)`  — record is cached and its record_type has a slicer_script
/// - `Some(False)` — record is cached but the record_type has no slicer_script
/// - `None`        — record is not in the cache yet; callers should defer
///
/// Records always have a `record_type` (backend invariant), so we don't
/// match on `record_type: None` — it falls into `None` alongside "not loaded".
fn slicer_script_status(record_id: String, shared: Shared) -> Option(Bool) {
  case dict.get(shared.cache.records, record_id) {
    Ok(models.Record(
      record_type: Some(models.RecordType(slicer_script: Some(_), ..)),
      ..,
    )) -> Some(True)
    Ok(models.Record(
      record_type: Some(models.RecordType(slicer_script: None, ..)),
      ..,
    )) -> Some(False)
    _ -> None
  }
}

fn slicer_error_msg(err: ApiError, fallback: String) -> String {
  case err {
    types.ServerError(502, _) -> "3D Slicer is not reachable. Is it running?"
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
  let timer_id = global.set_interval(10_000, fn() { dispatch(SlicerPing) })
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
      status_badge.render(record.status, shared.translate),
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
      viewer.record_viewer_buttons(
        shared.viewers,
        record.study_uid,
        record.series_uid,
        record.viewer_study_uids,
        record.viewer_series_uids,
        option.map(record.record_type, fn(rt) { rt.level }),
        option.map(record.record_type, fn(rt) { rt.viewer_mode })
          |> option.unwrap("single_series"),
        "btn btn-primary",
        fn(url, study_uid) { RequestPreload(url, study_uid) },
      ),
    ]),
    // Slicer toolbar (only if record type has slicer_script)
    render_slicer_toolbar(model, record, shared.translate),
    // Output files (only if record type defines any OUTPUT file_registry entries)
    render_output_files(record),
    // Admin workflow section (instance-mode dry-run / fire)
    render_workflow_section(model, shared),
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
      case permissions.can_delete_record(record, shared.user) {
        True ->
          html.button(
            [
              attribute.class("btn btn-danger"),
              event.on_click(RequestDelete),
            ],
            [html.text("Delete Record")],
          )
        False -> element.none()
      },
    ]),
  ])
}

fn render_slicer_toolbar(
  model: Model,
  record: Record,
  translate: fn(i18n.Key) -> String,
) -> Element(Msg) {
  let has_script = case record.record_type {
    Some(models.RecordType(slicer_script: Some(_), ..)) -> True
    _ -> False
  }

  use <- bool.guard(!has_script, element.none())

  let slicer_badge = case model.slicer_available {
    Some(True) ->
      html.span([attribute.class("badge badge-success")], [
        html.text(translate(i18n.ExecSlicerConnected)),
      ])
    Some(False) ->
      html.span([attribute.class("badge badge-danger")], [
        html.text(translate(i18n.ExecSlicerUnreachable)),
      ])
    None ->
      html.span([attribute.class("badge badge-pending")], [
        html.text(translate(i18n.ExecSlicerChecking)),
      ])
  }

  let btn_disabled =
    model.slicer_loading || model.slicer_available != Some(True)

  html.div([attribute.class("slicer-toolbar card")], [
    html.div([attribute.class("slicer-toolbar-header")], [
      html.h4([], [html.text("3D Slicer")]),
      slicer_badge,
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

fn render_output_files(record: Record) -> Element(Msg) {
  let output_defs = case record.record_type {
    Some(rt) ->
      case rt.file_registry {
        Some(defs) -> list.filter(defs, fn(d) { d.role == models.Output })
        None -> []
      }
    None -> []
  }

  case output_defs {
    [] -> element.none()
    defs ->
      html.div([attribute.class("output-files card")], [
        html.h4([], [html.text("Output Files")]),
        html.ul(
          [attribute.class("output-files-list")],
          list.map(defs, fn(file_def) {
            render_output_file_item(record, file_def)
          }),
        ),
      ])
  }
}

fn render_output_file_item(
  record: Record,
  file_def: models.FileDefinition,
) -> Element(Msg) {
  let label = case file_def.description {
    Some(desc) -> desc
    None -> file_def.name
  }

  let link_lookup = case record.file_links {
    Some(links) -> list.find(links, fn(l) { l.name == file_def.name })
    None -> Error(Nil)
  }

  let action = case link_lookup, record.id {
    Ok(file_link), Some(id) ->
      // target="_blank" — modem.init's global click handler treats this
      // anchor as external and skips preventDefault, letting the browser
      // perform the native download. Without it, modem routes the API URL
      // through the SPA router, which renders 404. The `download`
      // attribute keeps the file saving — no new tab actually opens.
      html.a(
        [
          attribute.class("btn btn-sm btn-outline"),
          attribute.href(records.output_file_download_url(
            int.to_string(id),
            file_def.name,
          )),
          attribute.target("_blank"),
          attribute.attribute("download", file_link.filename),
        ],
        [html.text("Download")],
      )
    _, _ ->
      html.span(
        [
          attribute.class("btn btn-sm btn-outline disabled"),
          attribute.attribute("aria-disabled", "true"),
          attribute.title("File not yet available"),
        ],
        [html.text("Not available")],
      )
  }

  html.li([attribute.class("output-file-item")], [
    html.span([attribute.class("output-file-name")], [html.text(label)]),
    action,
  ])
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
        decode.one_of(decode.at(["detail", "error"], decode.string), [
          decode.success("Submission failed"),
        ]),
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
                <> " ("
                <> study.date
                <> ")",
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
                format_series_label(series.modality, series.series_description)
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
    case record.context_info_html {
      Some(html_str) ->
        html.div(
          [
            attribute.class("context-info"),
            attribute.property("innerHTML", json.string(html_str)),
          ],
          [],
        )
      None -> element.none()
    },
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

// --- Admin workflow section ---

fn render_workflow_section(model: Model, shared: Shared) -> Element(Msg) {
  use <- bool.guard(!is_admin_user(shared), element.none())
  html.div([attribute.class("workflow-section card")], [
    html.div([attribute.class("workflow-section-header")], [
      html.h3([], [html.text("Workflow (admin)")]),
      html.p([attribute.class("text-muted")], [
        html.text(
          "Drag to pan, scroll to zoom. Click a record_type node to choose a "
          <> "trigger to dry-run, then confirm to fire.",
        ),
      ]),
    ]),
    load_status.render(
      model.workflow_load_status,
      fn() { workflow_loading_view() },
      fn() { workflow_loaded_view(model) },
      fn(msg) { workflow_error_view(msg, model.workflow_service_disabled) },
    ),
  ])
}

fn workflow_loading_view() -> Element(Msg) {
  html.div([attribute.class("workflow-loading")], [
    html.p([], [html.text("Loading workflow graph...")]),
  ])
}

fn workflow_error_view(message: String, service_disabled: Bool) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
    case service_disabled {
      True -> element.none()
      False ->
        html.button(
          [
            attribute.class("btn btn-primary"),
            event.on_click(WorkflowRetryLoad),
          ],
          [html.text("Retry")],
        )
    },
  ])
}

fn workflow_loaded_view(model: Model) -> Element(Msg) {
  case model.workflow_graph {
    Some(graph) ->
      html.div([attribute.class("workflow-layout")], [
        html.div([attribute.class("workflow-canvas")], [
          wf_renderer.render(
            graph,
            model.workflow_view,
            model.workflow_selected_node,
            model.workflow_selected_edge,
            wf_renderer.Handlers(
              on_node_click: WorkflowNodeClicked,
              on_edge_click: WorkflowEdgeClicked,
              on_pan_zoom: WorkflowPanZoom,
            ),
          ),
        ]),
        workflow_side_panel(model, graph),
      ])
    None -> workflow_loading_view()
  }
}

fn workflow_side_panel(model: Model, graph: WorkflowGraph) -> Element(Msg) {
  let body = case model.plan_state {
    PlanLoading(_) | PlanReady(_, _) | PlanFiring(_, _) | PlanFailed(_, _) ->
      dry_run_panel(model, graph)
    NoPlan ->
      case model.trigger_picker {
        PickerOpen(_, _, _) -> render_picker_panel(model, graph)
        NoPicker ->
          case model.workflow_selected_node, model.workflow_selected_edge {
            Some(node_id), _ ->
              case list.find(graph.nodes, fn(n) { n.id == node_id }) {
                Ok(node) ->
                  wf_renderer.node_panel(node, node_panel_footer(node, graph))
                Error(_) -> wf_renderer.empty_panel()
              }
            _, Some(edge_id) ->
              case list.find(graph.edges, fn(e) { e.id == edge_id }) {
                Ok(edge) ->
                  wf_renderer.edge_panel(edge, fire_hint(edge.trigger_kind))
                Error(_) -> wf_renderer.empty_panel()
              }
            _, _ -> wf_renderer.empty_panel()
          }
      }
  }
  html.aside([attribute.class("workflow-side-panel")], [
    html.div([attribute.class("workflow-side-panel-header")], [
      html.h4([], [html.text("Details")]),
      case model.workflow_selected_node, model.workflow_selected_edge {
        Some(_), _ | _, Some(_) ->
          html.button(
            [
              attribute.class("btn btn-sm btn-secondary"),
              event.on_click(WorkflowClearSelection),
            ],
            [html.text("Close")],
          )
        _, _ -> element.none()
      },
    ]),
    body,
  ])
}

fn fire_hint(kind: workflow_models.TriggerKind) -> Element(Msg) {
  case is_fireable_trigger(kind) {
    True ->
      html.p([attribute.class("text-muted")], [
        html.text("Fire this trigger from its source record_type node."),
      ])
    False ->
      html.p([attribute.class("text-muted")], [
        html.text("This trigger kind is not fireable from the UI."),
      ])
  }
}

/// Per-node footer rendered inside `wf_renderer.node_panel`. For record_type
/// nodes with at least one fireable outgoing edge we surface "Fire trigger…"
/// (the picker's secondary entry point — clicking the node on the canvas is
/// the primary one in `WorkflowNodeClicked`). When the record_type has no
/// fireable triggers we say so explicitly instead of leaving the user puzzled
/// by a non-reactive button.
fn node_panel_footer(node: WorkflowNode, graph: WorkflowGraph) -> Element(Msg) {
  case node.kind {
    RecordTypeNode ->
      case available_kinds_from_node(graph, node.id) {
        [] ->
          html.p([attribute.class("text-muted")], [
            html.text("No fireable triggers for this record type."),
          ])
        _ ->
          html.button(
            [
              attribute.class("btn btn-primary btn-sm"),
              event.on_click(WorkflowRecordTypeNodeClicked(node.id)),
            ],
            [html.text("Fire trigger…")],
          )
      }
    _ -> wf_renderer.expand_hint(node)
  }
}

fn dry_run_panel(model: Model, graph: WorkflowGraph) -> Element(Msg) {
  let trigger = current_trigger(model.plan_state)
  let label_text = case trigger {
    Some(t) -> node_label(graph, t.node_id)
    None -> ""
  }
  let body = case model.plan_state {
    PlanLoading(_) -> html.p([], [html.text("Planning trigger...")])
    PlanReady(_, plan) | PlanFiring(_, plan) -> plan_list_view(plan)
    PlanFailed(_, msg) ->
      html.div([attribute.class("error-container")], [
        html.p([attribute.class("error-message")], [html.text(msg)]),
      ])
    NoPlan -> element.none()
  }
  html.div([attribute.class("workflow-side-panel-body workflow-plan-panel")], [
    html.h5([], [html.text("Dry-run: " <> label_text)]),
    body,
    html.div(
      [attribute.class("workflow-plan-actions")],
      plan_panel_buttons(model.plan_state),
    ),
  ])
}

/// Human-friendly node label for the dry-run panel header — "<label> (<id>)",
/// falling back to just the id when the node is no longer in the graph (e.g.
/// after a schema change between dry-run and re-render).
fn node_label(graph: WorkflowGraph, node_id: String) -> String {
  case list.find(graph.nodes, fn(n) { n.id == node_id }) {
    Ok(n) -> n.label <> " (" <> node_id <> ")"
    Error(_) -> node_id
  }
}

fn render_picker_panel(model: Model, graph: WorkflowGraph) -> Element(Msg) {
  case model.trigger_picker {
    PickerOpen(node_id, current_kind, status_override) -> {
      let node_label = case list.find(graph.nodes, fn(n) { n.id == node_id }) {
        Ok(n) -> n.label
        Error(_) -> node_id
      }
      let kinds = available_kinds_from_node(graph, node_id)
      html.div(
        [attribute.class("workflow-side-panel-body workflow-trigger-picker")],
        [
          html.h5([], [html.text("Fire trigger for " <> node_label)]),
          html.div(
            [attribute.class("workflow-picker-kinds")],
            list.map(kinds, fn(k) { trigger_kind_radio(k, current_kind) }),
          ),
          case current_kind {
            StatusTrigger -> status_override_dropdown(status_override)
            _ -> element.none()
          },
          html.div([attribute.class("workflow-plan-actions")], [
            html.button(
              [
                attribute.class("btn btn-primary"),
                event.on_click(PickerPlanClicked),
              ],
              [html.text("Plan")],
            ),
            html.button(
              [
                attribute.class("btn btn-secondary"),
                event.on_click(PickerCancelled),
              ],
              [html.text("Cancel")],
            ),
          ]),
        ],
      )
    }
    NoPicker -> element.none()
  }
}

fn trigger_kind_radio(
  kind: TriggerKindRequest,
  current: TriggerKindRequest,
) -> Element(Msg) {
  let value_str = workflow_models.trigger_kind_request_to_string(kind)
  let radio_id = "picker-kind-" <> value_str
  html.label(
    [attribute.class("workflow-picker-radio-label"), attribute.for(radio_id)],
    [
      html.input([
        attribute.id(radio_id),
        attribute.type_("radio"),
        attribute.name("trigger-kind"),
        attribute.value(value_str),
        attribute.checked(kind == current),
        // `on_change` fires only when the radio becomes selected (browser
        // suppresses change events on un-selection within the same group), so
        // exactly one PickerKindChanged is dispatched per user click.
        event.on_change(fn(_) { PickerKindChanged(kind) }),
      ]),
      html.text(" " <> trigger_kind_request_label(kind)),
    ],
  )
}

fn trigger_kind_request_label(k: TriggerKindRequest) -> String {
  case k {
    StatusTrigger -> "Status"
    DataUpdateTrigger -> "Data update"
    FileChangeTrigger -> "File change"
  }
}

fn status_override_dropdown(current: Option(String)) -> Element(Msg) {
  let current_str = option.unwrap(current, "")
  let placeholder =
    html.option(
      [attribute.value(""), attribute.selected(current_str == "")],
      "(record's actual status)",
    )
  let status_options =
    list.map(status_utils.all_statuses(), fn(s) {
      let backend = status_utils.to_backend_string(s)
      html.option(
        [
          attribute.value(backend),
          attribute.selected(current_str == backend),
        ],
        status_utils.display_text(s),
      )
    })
  html.div([attribute.class("form-group")], [
    html.label([], [html.text("Status override")]),
    html.select(
      [
        attribute.class("form-control"),
        event.on_change(fn(value) {
          case value {
            "" -> PickerStatusOverrideChanged(None)
            v -> PickerStatusOverrideChanged(Some(v))
          }
        }),
      ],
      [placeholder, ..status_options],
    ),
  ])
}

fn current_trigger(state: PlanState) -> Option(PendingTrigger) {
  case state {
    PlanLoading(t) | PlanReady(t, _) | PlanFiring(t, _) | PlanFailed(t, _) ->
      Some(t)
    NoPlan -> None
  }
}

/// Confirm/Cancel buttons whose state derives from the plan state machine —
/// e.g. PlanFiring disables Confirm and labels it "Firing...", PlanFailed
/// shows "Re-run dry-run".
fn plan_panel_buttons(state: PlanState) -> List(Element(Msg)) {
  let cancel =
    html.button(
      [attribute.class("btn btn-secondary"), event.on_click(DismissPlan)],
      [html.text("Cancel")],
    )
  let primary = case state {
    PlanReady(_, _) ->
      html.button(
        [
          attribute.class("btn btn-primary"),
          event.on_click(ConfirmFireClicked),
        ],
        [html.text("Confirm and Fire")],
      )
    PlanFiring(_, _) ->
      html.button(
        [attribute.class("btn btn-primary"), attribute.disabled(True)],
        [html.text("Firing...")],
      )
    PlanLoading(_) ->
      html.button(
        [attribute.class("btn btn-primary"), attribute.disabled(True)],
        [html.text("Planning...")],
      )
    PlanFailed(t, _) ->
      html.button(
        [
          attribute.class("btn btn-primary"),
          event.on_click(RetryDryRun(t)),
        ],
        [html.text("Re-run dry-run")],
      )
    NoPlan ->
      html.button(
        [attribute.class("btn btn-primary"), attribute.disabled(True)],
        [html.text("Confirm and Fire")],
      )
  }
  [primary, cancel]
}

fn plan_list_view(plan: DryRunResponse) -> Element(Msg) {
  case plan.plan {
    [] ->
      html.p([attribute.class("text-muted")], [
        html.text("No actions would be dispatched (conditions did not match)."),
      ])
    actions ->
      html.ol(
        [attribute.class("workflow-plan-list")],
        list.map(actions, plan_action_view),
      )
  }
}

fn plan_action_view(action: ActionPreview) -> Element(Msg) {
  html.li([attribute.class("workflow-plan-action")], [
    html.div([attribute.class("workflow-plan-action-type")], [
      html.text(workflow_models.action_type_label(action.action_type)),
    ]),
    html.div([attribute.class("workflow-plan-action-summary")], [
      html.text(action.summary),
    ]),
  ])
}

fn is_fireable_trigger(kind: workflow_models.TriggerKind) -> Bool {
  case kind {
    TriggerOnStatus | TriggerOnDataUpdate | TriggerOnFileChange -> True
    _ -> False
  }
}
