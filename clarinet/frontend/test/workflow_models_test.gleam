// Round-trip tests for workflow API decoders.
//
// JSON samples mirror real responses from `tests/integration/test_workflow_router.py`
// — keep them in sync if the backend Pydantic shape changes.

import api/workflow_models.{
  ActionPreview, CallFunctionAction, CallFunctionDispatch, CreateRecordAction,
  CreateRecordEdge, DispatchPreview, FiringRecord, ParentRecordIdSource,
  PipelineDispatch, PipelineNode, Position, RecordTypeNode, TriggerOnStatus,
  WorkflowEdge, WorkflowNode,
}
import gleam/json
import gleam/list
import gleam/option.{None, Some}
import gleeunit
import gleeunit/should

pub fn main() {
  gleeunit.main()
}

// --- graph_decoder ---

pub fn graph_decoder_schema_test() {
  let json_str =
    "{\"nodes\":["
    <> "{\"id\":\"record_type:wf-parent\",\"kind\":\"record_type\","
    <> "\"label\":\"wf-parent\",\"position\":{\"x\":100.0,\"y\":50.0},"
    <> "\"metadata\":{},\"expandable\":false,\"expanded\":false},"
    <> "{\"id\":\"pipeline:p1\",\"kind\":\"pipeline\","
    <> "\"label\":\"p1\",\"position\":{\"x\":300.0,\"y\":50.0},"
    <> "\"metadata\":{},\"expandable\":true,\"expanded\":false}],"
    <> "\"edges\":["
    <> "{\"id\":\"edge-1\",\"from_node\":\"record_type:wf-parent\","
    <> "\"to_node\":\"record_type:wf-child\",\"kind\":\"create_record\","
    <> "\"trigger_kind\":\"on_status\",\"trigger_value\":\"finished\","
    <> "\"label\":null,\"condition_summary\":null,"
    <> "\"metadata\":{},\"firings\":[]}],"
    <> "\"width\":400.0,\"height\":100.0}"

  let assert Ok(graph) = json.parse(json_str, workflow_models.graph_decoder())

  should.equal(list.length(graph.nodes), 2)
  should.equal(list.length(graph.edges), 1)
  should.equal(graph.width, 400.0)
  should.equal(graph.height, 100.0)

  let assert [node1, node2] = graph.nodes
  should.equal(
    node1,
    WorkflowNode(
      id: "record_type:wf-parent",
      kind: RecordTypeNode,
      label: "wf-parent",
      position: Position(x: 100.0, y: 50.0),
      expandable: False,
      expanded: False,
    ),
  )
  should.equal(node2.kind, PipelineNode)
  should.equal(node2.expandable, True)

  let assert [edge] = graph.edges
  should.equal(
    edge,
    WorkflowEdge(
      id: "edge-1",
      from_node: "record_type:wf-parent",
      to_node: "record_type:wf-child",
      kind: CreateRecordEdge,
      trigger_kind: TriggerOnStatus,
      trigger_value: Some("finished"),
      label: None,
      condition_summary: None,
      firings: [],
    ),
  )
}

pub fn graph_decoder_instance_marks_fired_edge_test() {
  let json_str =
    "{\"nodes\":["
    <> "{\"id\":\"record_type:wf-parent\",\"kind\":\"record_type\","
    <> "\"label\":\"wf-parent\",\"position\":{\"x\":0.0,\"y\":0.0},"
    <> "\"metadata\":{},\"expandable\":false,\"expanded\":false}],"
    <> "\"edges\":["
    <> "{\"id\":\"edge-1\",\"from_node\":\"record_type:wf-parent\","
    <> "\"to_node\":\"record_type:wf-child\",\"kind\":\"create_record\","
    <> "\"trigger_kind\":\"on_status\",\"trigger_value\":\"finished\","
    <> "\"label\":null,\"condition_summary\":null,\"metadata\":{},"
    <> "\"firings\":[{"
    <> "\"fired_at\":\"2025-05-12T10:30:45.123456+00:00\","
    <> "\"source\":\"parent_record_id\","
    <> "\"metadata\":{\"child_record_id\":456}}]}],"
    <> "\"width\":100.0,\"height\":50.0}"

  let assert Ok(graph) = json.parse(json_str, workflow_models.graph_decoder())
  let assert [edge] = graph.edges
  let assert [firing] = edge.firings
  should.equal(
    firing,
    FiringRecord(
      fired_at: "2025-05-12T10:30:45.123456+00:00",
      source: ParentRecordIdSource,
    ),
  )
}

/// optional_field defaults: a node missing `expandable`/`expanded` must
/// fall back to `False`. Backend always emits them, but `node_decoder`
/// (workflow_models.gleam:236-237) declares them optional — this test
/// pins the contract so removing the default silently is caught.
pub fn graph_decoder_node_optional_defaults_test() {
  let json_str =
    "{\"nodes\":[{\"id\":\"a\",\"kind\":\"record_type\","
    <> "\"label\":\"A\",\"position\":{\"x\":1.0,\"y\":2.0}}],"
    <> "\"edges\":[],\"width\":0.0,\"height\":0.0}"

  let assert Ok(graph) = json.parse(json_str, workflow_models.graph_decoder())
  let assert [node] = graph.nodes
  should.equal(node.expandable, False)
  should.equal(node.expanded, False)
}

pub fn graph_decoder_empty_test() {
  let json_str = "{\"nodes\":[],\"edges\":[],\"width\":0.0,\"height\":0.0}"
  let assert Ok(graph) = json.parse(json_str, workflow_models.graph_decoder())
  should.equal(graph.nodes, [])
  should.equal(graph.edges, [])
  should.equal(graph.width, 0.0)
  should.equal(graph.height, 0.0)
}

// --- dry_run_decoder ---

pub fn dry_run_decoder_test() {
  let json_str =
    "{\"digest\":\"abcd1234efgh5678\",\"plan\":[{"
    <> "\"action_type\":\"create_record\","
    <> "\"summary\":\"Create record 'wf-child'\","
    <> "\"target\":\"wf-child\","
    <> "\"trigger_record_id\":123,"
    <> "\"trigger_record_type\":\"wf-parent\","
    <> "\"patient_id\":\"WF_PAT\","
    <> "\"study_uid\":\"1.2.3.7000\","
    <> "\"series_uid\":null,"
    <> "\"file_name\":null}]}"

  let assert Ok(dry_run) =
    json.parse(json_str, workflow_models.dry_run_decoder())
  should.equal(dry_run.digest, "abcd1234efgh5678")

  let assert [action] = dry_run.plan
  should.equal(
    action,
    ActionPreview(
      action_type: CreateRecordAction,
      summary: "Create record 'wf-child'",
      target: Some("wf-child"),
      trigger_record_id: Some(123),
      trigger_record_type: Some("wf-parent"),
      patient_id: Some("WF_PAT"),
      study_uid: Some("1.2.3.7000"),
      series_uid: None,
      file_name: None,
    ),
  )
}

/// ActionPreview where every nullable field is absent (action types like
/// `call_function` that don't carry record context). All Option fields
/// must default to None — the optional_field decoders guarantee this.
pub fn dry_run_decoder_action_minimal_test() {
  let json_str =
    "{\"digest\":\"noopnoopnoopnoop\",\"plan\":[{"
    <> "\"action_type\":\"call_function\","
    <> "\"summary\":\"Call my_function\"}]}"

  let assert Ok(dry_run) =
    json.parse(json_str, workflow_models.dry_run_decoder())
  let assert [action] = dry_run.plan
  should.equal(action.action_type, CallFunctionAction)
  should.equal(action.target, None)
  should.equal(action.trigger_record_id, None)
  should.equal(action.trigger_record_type, None)
  should.equal(action.patient_id, None)
  should.equal(action.study_uid, None)
  should.equal(action.series_uid, None)
  should.equal(action.file_name, None)
}

// --- fire_decoder ---

pub fn fire_decoder_test() {
  let json_str =
    "{\"executed_actions\":[{"
    <> "\"action_type\":\"create_record\","
    <> "\"summary\":\"Created wf-child\","
    <> "\"target\":\"wf-child\"}]}"

  let assert Ok(fire) = json.parse(json_str, workflow_models.fire_decoder())
  let assert [action] = fire.executed_actions
  should.equal(action.action_type, CreateRecordAction)
  should.equal(action.target, Some("wf-child"))
  should.equal(action.summary, "Created wf-child")
}

// --- pipeline_name_from_id helper ---

pub fn pipeline_name_from_id_test() {
  should.equal(
    workflow_models.pipeline_name_from_id("pipeline:p1"),
    Some("p1"),
  )
  should.equal(
    workflow_models.pipeline_name_from_id("record_type:wf-parent"),
    None,
  )
  should.equal(workflow_models.pipeline_name_from_id("plain"), None)
}

// --- dispatch_dry_run_decoder ---

pub fn dispatch_dry_run_decoder_call_function_test() {
  let json_str =
    "{\"preview\":{\"kind\":\"call_function\","
    <> "\"node_id\":\"call:tasks.scripts.foo.my_func\","
    <> "\"label\":\"call my_func\","
    <> "\"record_id\":42,"
    <> "\"payload_preview\":{\"function_name\":\"my_func\"}},"
    <> "\"digest\":\"abcdef0123456789\"}"

  let assert Ok(resp) =
    json.parse(json_str, workflow_models.dispatch_dry_run_decoder())
  should.equal(resp.digest, "abcdef0123456789")
  should.equal(
    resp.preview,
    DispatchPreview(
      kind: CallFunctionDispatch,
      node_id: "call:tasks.scripts.foo.my_func",
      label: "call my_func",
      record_id: 42,
    ),
  )
}

pub fn dispatch_dry_run_decoder_pipeline_test() {
  let json_str =
    "{\"preview\":{\"kind\":\"pipeline\","
    <> "\"node_id\":\"pipeline:ct_seg\","
    <> "\"label\":\"pipeline ct_seg\","
    <> "\"record_id\":1,"
    <> "\"payload_preview\":{\"pipeline_name\":\"ct_seg\",\"step_count\":3}},"
    <> "\"digest\":\"1111111111111111\"}"

  let assert Ok(resp) =
    json.parse(json_str, workflow_models.dispatch_dry_run_decoder())
  should.equal(resp.preview.kind, PipelineDispatch)
  should.equal(resp.preview.node_id, "pipeline:ct_seg")
  should.equal(resp.digest, "1111111111111111")
}

// --- dispatch_decoder ---

pub fn dispatch_decoder_test() {
  let json_str =
    "{\"preview\":{\"kind\":\"pipeline\","
    <> "\"node_id\":\"pipeline:p1\","
    <> "\"label\":\"pipeline p1\","
    <> "\"record_id\":7,"
    <> "\"payload_preview\":{\"pipeline_name\":\"p1\",\"step_count\":2}},"
    <> "\"task_id\":\"task-abc-123\"}"

  let assert Ok(resp) = json.parse(json_str, workflow_models.dispatch_decoder())
  should.equal(resp.task_id, "task-abc-123")
  should.equal(resp.preview.kind, PipelineDispatch)
}

pub fn is_dispatchable_node_test() {
  should.equal(workflow_models.is_dispatchable_node("call:mod.fn"), True)
  should.equal(workflow_models.is_dispatchable_node("pipeline:p1"), True)
  should.equal(
    workflow_models.is_dispatchable_node("pipeline_step:p1::0"),
    False,
  )
  should.equal(
    workflow_models.is_dispatchable_node("record_type:foo"),
    False,
  )
  should.equal(workflow_models.is_dispatchable_node("entity:series"), False)
  should.equal(workflow_models.is_dispatchable_node("plain"), False)
}
