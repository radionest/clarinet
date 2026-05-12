// Frontend mirror of `clarinet.services.workflow_graph.models` and
// `clarinet.services.recordflow.action_preview` Pydantic models.
//
// Backend types carry `metadata` / `details` (`dict[str, Any]`) on Node,
// Edge, FiringRecord and ActionPreview for free-form per-instance payloads.
// The frontend currently doesn't render any of them — they are silently
// dropped during decoding. Re-introduce when the UI actually consumes them.

import gleam/dynamic/decode
import gleam/option.{type Option, None, Some}
import gleam/string

// --- Enums ---

pub type NodeKind {
  RecordTypeNode
  EntityNode
  FileNode
  PipelineNode
  PipelineStepNode
  CallFunctionNode
}

pub type EdgeKind {
  CreateRecordEdge
  UpdateRecordEdge
  InvalidateEdge
  CallFunctionEdge
  PipelineDispatchEdge
  PipelineStepChainEdge
}

pub type TriggerKind {
  TriggerOnStatus
  TriggerOnDataUpdate
  TriggerOnFileChange
  TriggerOnCreated
  TriggerOnFileUpdate
  TriggerNone
}

pub type FiringSource {
  ParentRecordIdSource
  PipelineAuditSource
  InvalidationAuditSource
  StatusAuditSource
}

pub type ActionType {
  CreateRecordAction
  UpdateRecordAction
  InvalidateRecordsAction
  CallFunctionAction
  PipelineAction
}

/// Backend-facing trigger kind for `/dry-run` and `/fire` request bodies.
/// Distinct from `TriggerKind` on edges (the edge type carries the broader
/// `on_created` / `on_file_update` / `none` cases that have no plan_* entry
/// point — see `clarinet/api/routers/workflow.py`).
pub type TriggerKindRequest {
  StatusTrigger
  DataUpdateTrigger
  FileChangeTrigger
}

// --- Records ---

pub type Position {
  Position(x: Float, y: Float)
}

pub type FiringRecord {
  FiringRecord(fired_at: String, source: FiringSource)
}

pub type WorkflowNode {
  WorkflowNode(
    id: String,
    kind: NodeKind,
    label: String,
    position: Position,
    expandable: Bool,
    expanded: Bool,
  )
}

pub type WorkflowEdge {
  WorkflowEdge(
    id: String,
    from_node: String,
    to_node: String,
    kind: EdgeKind,
    trigger_kind: TriggerKind,
    trigger_value: Option(String),
    label: Option(String),
    condition_summary: Option(String),
    firings: List(FiringRecord),
  )
}

pub type WorkflowGraph {
  WorkflowGraph(
    nodes: List(WorkflowNode),
    edges: List(WorkflowEdge),
    width: Float,
    height: Float,
  )
}

pub type ActionPreview {
  ActionPreview(
    action_type: ActionType,
    summary: String,
    target: Option(String),
    trigger_record_id: Option(Int),
    trigger_record_type: Option(String),
    patient_id: Option(String),
    study_uid: Option(String),
    series_uid: Option(String),
    file_name: Option(String),
  )
}

pub type DryRunResponse {
  DryRunResponse(plan: List(ActionPreview), digest: String)
}

pub type FireResponse {
  FireResponse(executed_actions: List(ActionPreview))
}

// --- Encoders ---

pub fn trigger_kind_request_to_string(t: TriggerKindRequest) -> String {
  case t {
    StatusTrigger -> "status"
    DataUpdateTrigger -> "data_update"
    FileChangeTrigger -> "file_change"
  }
}

// --- Display labels ---

pub fn node_kind_label(kind: NodeKind) -> String {
  case kind {
    RecordTypeNode -> "Record type"
    EntityNode -> "Entity factory"
    FileNode -> "Project file"
    PipelineNode -> "Pipeline"
    PipelineStepNode -> "Pipeline step"
    CallFunctionNode -> "Custom function"
  }
}

pub fn edge_kind_label(kind: EdgeKind) -> String {
  case kind {
    CreateRecordEdge -> "Create record"
    UpdateRecordEdge -> "Update record"
    InvalidateEdge -> "Invalidate"
    CallFunctionEdge -> "Call function"
    PipelineDispatchEdge -> "Pipeline dispatch"
    PipelineStepChainEdge -> "Pipeline step chain"
  }
}

pub fn trigger_kind_label(kind: TriggerKind) -> String {
  case kind {
    TriggerOnStatus -> "on_status"
    TriggerOnDataUpdate -> "on_data_update"
    TriggerOnFileChange -> "on_file_change"
    TriggerOnCreated -> "on_created"
    TriggerOnFileUpdate -> "on_file_update"
    TriggerNone -> "none"
  }
}

pub fn action_type_label(t: ActionType) -> String {
  case t {
    CreateRecordAction -> "Create record"
    UpdateRecordAction -> "Update record"
    InvalidateRecordsAction -> "Invalidate"
    CallFunctionAction -> "Call function"
    PipelineAction -> "Pipeline"
  }
}

/// Extract the pipeline name from a `WorkflowNode.id`.
///
/// Backend `make_pipeline_id` (services/workflow_graph/models.py) emits
/// `"pipeline:{name}"` for pipeline nodes. Using this prefix instead of
/// `node.label` keeps expansion stable if a future change reshapes the
/// label (e.g. adds a step count). Returns `None` for non-pipeline nodes
/// so callers can no-op on stale clicks.
pub fn pipeline_name_from_id(id: String) -> Option(String) {
  case string.split_once(id, on: ":") {
    Ok(#("pipeline", name)) -> Some(name)
    _ -> None
  }
}

// --- Decoders ---

pub fn graph_decoder() -> decode.Decoder(WorkflowGraph) {
  use nodes <- decode.field("nodes", decode.list(node_decoder()))
  use edges <- decode.field("edges", decode.list(edge_decoder()))
  use width <- decode.field("width", decode.float)
  use height <- decode.field("height", decode.float)
  decode.success(WorkflowGraph(
    nodes: nodes,
    edges: edges,
    width: width,
    height: height,
  ))
}

pub fn dry_run_decoder() -> decode.Decoder(DryRunResponse) {
  use plan <- decode.field("plan", decode.list(action_preview_decoder()))
  use digest <- decode.field("digest", decode.string)
  decode.success(DryRunResponse(plan: plan, digest: digest))
}

pub fn fire_decoder() -> decode.Decoder(FireResponse) {
  use executed_actions <- decode.field(
    "executed_actions",
    decode.list(action_preview_decoder()),
  )
  decode.success(FireResponse(executed_actions: executed_actions))
}

fn node_decoder() -> decode.Decoder(WorkflowNode) {
  use id <- decode.field("id", decode.string)
  use kind <- decode.field("kind", node_kind_decoder())
  use label <- decode.field("label", decode.string)
  use position <- decode.field("position", position_decoder())
  use expandable <- decode.optional_field("expandable", False, decode.bool)
  use expanded <- decode.optional_field("expanded", False, decode.bool)
  decode.success(WorkflowNode(
    id: id,
    kind: kind,
    label: label,
    position: position,
    expandable: expandable,
    expanded: expanded,
  ))
}

fn edge_decoder() -> decode.Decoder(WorkflowEdge) {
  use id <- decode.field("id", decode.string)
  use from_node <- decode.field("from_node", decode.string)
  use to_node <- decode.field("to_node", decode.string)
  use kind <- decode.field("kind", edge_kind_decoder())
  use trigger_kind <- decode.optional_field(
    "trigger_kind",
    TriggerNone,
    trigger_kind_decoder(),
  )
  use trigger_value <- decode.optional_field(
    "trigger_value",
    None,
    decode.optional(decode.string),
  )
  use label <- decode.optional_field(
    "label",
    None,
    decode.optional(decode.string),
  )
  use condition_summary <- decode.optional_field(
    "condition_summary",
    None,
    decode.optional(decode.string),
  )
  use firings <- decode.optional_field(
    "firings",
    [],
    decode.list(firing_record_decoder()),
  )
  decode.success(WorkflowEdge(
    id: id,
    from_node: from_node,
    to_node: to_node,
    kind: kind,
    trigger_kind: trigger_kind,
    trigger_value: trigger_value,
    label: label,
    condition_summary: condition_summary,
    firings: firings,
  ))
}

fn position_decoder() -> decode.Decoder(Position) {
  use x <- decode.field("x", decode.float)
  use y <- decode.field("y", decode.float)
  decode.success(Position(x: x, y: y))
}

fn firing_record_decoder() -> decode.Decoder(FiringRecord) {
  use fired_at <- decode.field("fired_at", decode.string)
  use source <- decode.field("source", firing_source_decoder())
  decode.success(FiringRecord(fired_at: fired_at, source: source))
}

fn action_preview_decoder() -> decode.Decoder(ActionPreview) {
  use action_type <- decode.field("action_type", action_type_decoder())
  use summary <- decode.field("summary", decode.string)
  use target <- decode.optional_field(
    "target",
    None,
    decode.optional(decode.string),
  )
  use trigger_record_id <- decode.optional_field(
    "trigger_record_id",
    None,
    decode.optional(decode.int),
  )
  use trigger_record_type <- decode.optional_field(
    "trigger_record_type",
    None,
    decode.optional(decode.string),
  )
  use patient_id <- decode.optional_field(
    "patient_id",
    None,
    decode.optional(decode.string),
  )
  use study_uid <- decode.optional_field(
    "study_uid",
    None,
    decode.optional(decode.string),
  )
  use series_uid <- decode.optional_field(
    "series_uid",
    None,
    decode.optional(decode.string),
  )
  use file_name <- decode.optional_field(
    "file_name",
    None,
    decode.optional(decode.string),
  )
  decode.success(ActionPreview(
    action_type: action_type,
    summary: summary,
    target: target,
    trigger_record_id: trigger_record_id,
    trigger_record_type: trigger_record_type,
    patient_id: patient_id,
    study_uid: study_uid,
    series_uid: series_uid,
    file_name: file_name,
  ))
}

fn node_kind_decoder() -> decode.Decoder(NodeKind) {
  use raw <- decode.then(decode.string)
  case raw {
    "record_type" -> decode.success(RecordTypeNode)
    "entity" -> decode.success(EntityNode)
    "file" -> decode.success(FileNode)
    "pipeline" -> decode.success(PipelineNode)
    "pipeline_step" -> decode.success(PipelineStepNode)
    "call_function" -> decode.success(CallFunctionNode)
    _ -> decode.failure(RecordTypeNode, "NodeKind")
  }
}

fn edge_kind_decoder() -> decode.Decoder(EdgeKind) {
  use raw <- decode.then(decode.string)
  case raw {
    "create_record" -> decode.success(CreateRecordEdge)
    "update_record" -> decode.success(UpdateRecordEdge)
    "invalidate" -> decode.success(InvalidateEdge)
    "call_function" -> decode.success(CallFunctionEdge)
    "pipeline_dispatch" -> decode.success(PipelineDispatchEdge)
    "pipeline_step_chain" -> decode.success(PipelineStepChainEdge)
    _ -> decode.failure(CreateRecordEdge, "EdgeKind")
  }
}

fn trigger_kind_decoder() -> decode.Decoder(TriggerKind) {
  use raw <- decode.then(decode.string)
  case raw {
    "on_status" -> decode.success(TriggerOnStatus)
    "on_data_update" -> decode.success(TriggerOnDataUpdate)
    "on_file_change" -> decode.success(TriggerOnFileChange)
    "on_created" -> decode.success(TriggerOnCreated)
    "on_file_update" -> decode.success(TriggerOnFileUpdate)
    "none" -> decode.success(TriggerNone)
    _ -> decode.failure(TriggerNone, "TriggerKind")
  }
}

fn firing_source_decoder() -> decode.Decoder(FiringSource) {
  use raw <- decode.then(decode.string)
  case raw {
    "parent_record_id" -> decode.success(ParentRecordIdSource)
    "pipeline_audit" -> decode.success(PipelineAuditSource)
    "invalidation_audit" -> decode.success(InvalidationAuditSource)
    "status_audit" -> decode.success(StatusAuditSource)
    _ -> decode.failure(ParentRecordIdSource, "FiringSource")
  }
}

fn action_type_decoder() -> decode.Decoder(ActionType) {
  use raw <- decode.then(decode.string)
  case raw {
    "create_record" -> decode.success(CreateRecordAction)
    "update_record" -> decode.success(UpdateRecordAction)
    "invalidate_records" -> decode.success(InvalidateRecordsAction)
    "call_function" -> decode.success(CallFunctionAction)
    "pipeline" -> decode.success(PipelineAction)
    _ -> decode.failure(CreateRecordAction, "ActionType")
  }
}
