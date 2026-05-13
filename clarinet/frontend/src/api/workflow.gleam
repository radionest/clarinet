// Admin workflow visualization API client.
//
// Wraps `GET /api/admin/workflow/graph`, `POST /dry-run`, `POST /fire`
// (admin-only; 503 when `recordflow_enabled=False`).

import api/http_client
import api/types.{type ApiError}
import api/workflow_models.{
  type DispatchDryRunResponse, type DispatchResponse, type DryRunResponse,
  type FireResponse, type TriggerKindRequest, type WorkflowGraph,
}
import gleam/int
import gleam/javascript/promise.{type Promise}
import gleam/json
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/result
import gleam/string
import utils/load_status.{type LoadStatus}

const base_path = "/admin/workflow"

/// Graph scope.
///
/// - `Schema`: project-wide graph (every record_type, entity, file flow).
/// - `Instance`: subgraph centered on `record_id`'s record_type — parents
///   (types that can create it) + children (types it can create) plus all
///   intermediate pipeline / call / entity / file nodes between them.
///   Requires `record_id` (backend returns 422 otherwise).
pub type Scope {
  Schema
  Instance
}

/// Fetch the workflow graph.
///
/// `scope=Schema` (default for the admin page) returns the full graph;
/// `scope=Instance` (used by the per-record execute page) returns the
/// subgraph around the record's type. When `record_id` is set the edges
/// always carry firing history reconstructed from `parent_record_id` —
/// independently of `scope`. `expanded` is a list of pipeline names to
/// inline as `PIPELINE_STEP` nodes (empty means all pipelines collapsed).
pub fn get_graph(
  record_id: Option(Int),
  expanded: List(String),
  scope: Scope,
) -> Promise(Result(WorkflowGraph, ApiError)) {
  let path = base_path <> "/graph" <> build_query(record_id, expanded, scope)
  http_client.get(path)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      workflow_models.graph_decoder(),
      "Invalid workflow graph data",
    ))
  })
}

/// Plan what would happen if a trigger fired, without executing.
/// Returns the list of actions + a stable digest for `/fire`.
pub fn dry_run(
  record_id: Int,
  trigger_kind: TriggerKindRequest,
  status_override: Option(String),
) -> Promise(Result(DryRunResponse, ApiError)) {
  let body = trigger_body(record_id, trigger_kind, status_override, None)
  http_client.post(base_path <> "/dry-run", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      workflow_models.dry_run_decoder(),
      "Invalid dry-run response",
    ))
  })
}

/// Execute a previously-planned trigger. The digest from `dry_run` must
/// match the live re-plan. Common 409 causes (shown in toast via `detail`):
/// `WORKFLOW_PLAN_CHANGED` (state drifted) or `WORKFLOW_DIGEST_ALREADY_USED`
/// (double-click within 5-min TTL). Both require a fresh `/dry-run`.
pub fn fire(
  record_id: Int,
  trigger_kind: TriggerKindRequest,
  status_override: Option(String),
  plan_digest: String,
) -> Promise(Result(FireResponse, ApiError)) {
  let body = trigger_body(record_id, trigger_kind, status_override, Some(plan_digest))
  http_client.post(base_path <> "/fire", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      workflow_models.fire_decoder(),
      "Invalid fire response",
    ))
  })
}

/// Plan a direct enqueue of one `call:*` or `pipeline:*` node — returns
/// preview + digest for `/dispatch`. 404 if the node id is unknown to the
/// server (registry or pipeline map); 422 if the node kind is not
/// dispatchable.
pub fn dispatch_dry_run(
  node_id: String,
  record_id: Int,
) -> Promise(Result(DispatchDryRunResponse, ApiError)) {
  let body = dispatch_body(node_id, record_id, None)
  http_client.post(base_path <> "/dispatch-dry-run", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      workflow_models.dispatch_dry_run_decoder(),
      "Invalid dispatch-dry-run response",
    ))
  })
}

/// Enqueue the planned action into TaskIQ after digest verification.
/// Same idempotency / replay caveats as `/fire`.
pub fn dispatch(
  node_id: String,
  record_id: Int,
  plan_digest: String,
) -> Promise(Result(DispatchResponse, ApiError)) {
  let body = dispatch_body(node_id, record_id, Some(plan_digest))
  http_client.post(base_path <> "/dispatch", body)
  |> promise.map(fn(res) {
    result.try(res, http_client.decode_response(
      _,
      workflow_models.dispatch_decoder(),
      "Invalid dispatch response",
    ))
  })
}

/// Classify a workflow-load error into a `(LoadStatus, service_disabled)`
/// pair shared by the admin page and the in-record workflow section.
/// 503 from `_require_engine` indicates `recordflow_enabled=False` —
/// service_disabled=True so the page can hide the "Retry" button.
pub fn classify_load_error(err: ApiError) -> #(LoadStatus, Bool) {
  case err {
    types.ServerError(503, msg) -> #(load_status.Failed(msg), True)
    types.StructuredError(_, msg, _) -> #(load_status.Failed(msg), False)
    types.ServerError(_, msg) -> #(load_status.Failed(msg), False)
    _ -> #(load_status.Failed("Failed to load workflow graph"), False)
  }
}

fn trigger_body(
  record_id: Int,
  trigger_kind: TriggerKindRequest,
  status_override: Option(String),
  plan_digest: Option(String),
) -> String {
  let trigger_value =
    workflow_models.trigger_kind_request_to_string(trigger_kind)
  let base = [
    #("record_id", json.int(record_id)),
    #("trigger_kind", json.string(trigger_value)),
  ]
  let with_status = case status_override {
    Some(s) -> [#("status_override", json.string(s)), ..base]
    None -> base
  }
  let with_digest = case plan_digest {
    Some(d) -> [#("plan_digest", json.string(d)), ..with_status]
    None -> with_status
  }
  json.object(with_digest) |> json.to_string
}

fn dispatch_body(
  node_id: String,
  record_id: Int,
  plan_digest: Option(String),
) -> String {
  let base = [
    #("node_id", json.string(node_id)),
    #("record_id", json.int(record_id)),
  ]
  let with_digest = case plan_digest {
    Some(d) -> [#("plan_digest", json.string(d)), ..base]
    None -> base
  }
  json.object(with_digest) |> json.to_string
}

fn build_query(
  record_id: Option(Int),
  expanded: List(String),
  scope: Scope,
) -> String {
  let scope_param = case scope {
    Schema -> None
    // Backend defaults to schema, so omit ?scope=schema to keep URLs
    // backwards-compatible and avoid surprising the router on rollback.
    Instance -> Some("scope=instance")
  }
  let parts = [
    case record_id {
      Some(id) -> Some("record_id=" <> int.to_string(id))
      None -> None
    },
    case expanded {
      [] -> None
      _ -> Some("expanded=" <> string.join(expanded, ","))
    },
    scope_param,
  ]
  let kept =
    list.filter_map(parts, fn(p) {
      case p {
        Some(s) -> Ok(s)
        None -> Error(Nil)
      }
    })
  case kept {
    [] -> ""
    _ -> "?" <> string.join(kept, "&")
  }
}
