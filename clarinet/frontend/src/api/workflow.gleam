// Admin workflow visualization API client.
//
// Wraps `GET /api/admin/workflow/graph`, `POST /dry-run`, `POST /fire`
// (admin-only; 503 when `recordflow_enabled=False`).

import api/http_client
import api/types.{type ApiError}
import api/workflow_models.{
  type DryRunResponse, type FireResponse, type TriggerKindRequest,
  type WorkflowGraph,
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

/// Fetch the workflow graph.
///
/// Without `record_id` returns the project-wide schema. With `record_id`,
/// edges carry firing history reconstructed from `parent_record_id`.
/// `expanded` is a list of pipeline names to inline as `PIPELINE_STEP`
/// nodes — empty means all pipelines collapsed.
pub fn get_graph(
  record_id: Option(Int),
  expanded: List(String),
) -> Promise(Result(WorkflowGraph, ApiError)) {
  let path = base_path <> "/graph" <> build_query(record_id, expanded)
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

fn build_query(
  record_id: Option(Int),
  expanded: List(String),
) -> String {
  let parts = [
    case record_id {
      Some(id) -> Some("record_id=" <> int.to_string(id))
      None -> None
    },
    case expanded {
      [] -> None
      _ -> Some("expanded=" <> string.join(expanded, ","))
    },
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
