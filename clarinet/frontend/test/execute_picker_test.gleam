// Unit tests for the pure trigger-picker helpers in `pages/records/execute`.
// State-machine behaviour (update arms, view) is exercised end-to-end via the
// admin workflow on the execute page — only the building blocks are unit-tested.
import api/workflow_models.{
  type TriggerKind, type WorkflowEdge, CreateRecordEdge, DataUpdateTrigger,
  FileChangeTrigger, StatusTrigger, TriggerNone, TriggerOnCreated,
  TriggerOnDataUpdate, TriggerOnFileChange, TriggerOnFileUpdate, TriggerOnStatus,
  WorkflowEdge, WorkflowGraph,
}
import gleam/list
import gleam/option.{None, Some}
import gleeunit/should
import pages/records/execute

fn make_edge(
  id: String,
  from_node: String,
  trigger_kind: TriggerKind,
  trigger_value: option.Option(String),
) -> WorkflowEdge {
  WorkflowEdge(
    id: id,
    from_node: from_node,
    to_node: "record_type:child",
    kind: CreateRecordEdge,
    trigger_kind: trigger_kind,
    trigger_value: trigger_value,
    label: None,
    condition_summary: None,
    firings: [],
  )
}

fn make_graph(edges: List(WorkflowEdge)) -> workflow_models.WorkflowGraph {
  WorkflowGraph(nodes: [], edges: edges, width: 100.0, height: 100.0)
}

// --- available_kinds_from_node ---

pub fn available_kinds_empty_when_no_edges_test() {
  execute.available_kinds_from_node(make_graph([]), "record_type:parent")
  |> should.equal([])
}

pub fn available_kinds_ignores_edges_from_other_nodes_test() {
  let graph =
    make_graph([
      make_edge("e1", "record_type:other", TriggerOnStatus, Some("finished")),
    ])
  execute.available_kinds_from_node(graph, "record_type:parent")
  |> should.equal([])
}

pub fn available_kinds_filters_non_fireable_test() {
  let graph =
    make_graph([
      make_edge("e1", "record_type:p", TriggerOnCreated, None),
      make_edge("e2", "record_type:p", TriggerOnFileUpdate, None),
      make_edge("e3", "record_type:p", TriggerNone, None),
      make_edge("e4", "record_type:p", TriggerOnDataUpdate, None),
    ])
  // Only DataUpdate survives — Created/FileUpdate/None are not fireable.
  execute.available_kinds_from_node(graph, "record_type:p")
  |> should.equal([DataUpdateTrigger])
}

pub fn available_kinds_deduplicates_test() {
  let graph =
    make_graph([
      make_edge("e1", "record_type:p", TriggerOnStatus, Some("finished")),
      make_edge("e2", "record_type:p", TriggerOnStatus, Some("failed")),
    ])
  execute.available_kinds_from_node(graph, "record_type:p")
  |> should.equal([StatusTrigger])
}

pub fn available_kinds_includes_all_three_kinds_test() {
  let graph =
    make_graph([
      make_edge("e1", "record_type:p", TriggerOnFileChange, None),
      make_edge("e2", "record_type:p", TriggerOnStatus, Some("finished")),
      make_edge("e3", "record_type:p", TriggerOnDataUpdate, None),
    ])
  let kinds = execute.available_kinds_from_node(graph, "record_type:p")
  // Order is implementation-defined; assert membership.
  should.be_true(list.contains(kinds, StatusTrigger))
  should.be_true(list.contains(kinds, DataUpdateTrigger))
  should.be_true(list.contains(kinds, FileChangeTrigger))
  should.equal(list.length(kinds), 3)
}

// --- default_trigger_kind ---

pub fn default_trigger_kind_empty_test() {
  execute.default_trigger_kind([])
  |> should.equal(None)
}

pub fn default_trigger_kind_prefers_status_test() {
  execute.default_trigger_kind([
    FileChangeTrigger,
    DataUpdateTrigger,
    StatusTrigger,
  ])
  |> should.equal(Some(StatusTrigger))
}

pub fn default_trigger_kind_falls_back_to_data_update_test() {
  execute.default_trigger_kind([FileChangeTrigger, DataUpdateTrigger])
  |> should.equal(Some(DataUpdateTrigger))
}

pub fn default_trigger_kind_falls_back_to_file_change_test() {
  execute.default_trigger_kind([FileChangeTrigger])
  |> should.equal(Some(FileChangeTrigger))
}

// --- default_status_override_for_node ---

pub fn default_status_override_picks_first_status_edge_test() {
  let graph =
    make_graph([
      make_edge("e1", "record_type:p", TriggerOnDataUpdate, None),
      make_edge("e2", "record_type:p", TriggerOnStatus, Some("finished")),
      make_edge("e3", "record_type:p", TriggerOnStatus, Some("failed")),
    ])
  execute.default_status_override_for_node(graph, "record_type:p")
  |> should.equal(Some("finished"))
}

pub fn default_status_override_none_when_no_status_edges_test() {
  let graph =
    make_graph([
      make_edge("e1", "record_type:p", TriggerOnDataUpdate, None),
      make_edge("e2", "record_type:p", TriggerOnFileChange, None),
    ])
  execute.default_status_override_for_node(graph, "record_type:p")
  |> should.equal(None)
}

pub fn default_status_override_none_for_wildcard_status_edge_test() {
  // A status edge with `trigger_value=None` means "any status" — the picker
  // should leave the dropdown at "(record's actual status)" rather than
  // pre-fill a non-existent value.
  let graph =
    make_graph([make_edge("e1", "record_type:p", TriggerOnStatus, None)])
  execute.default_status_override_for_node(graph, "record_type:p")
  |> should.equal(None)
}
