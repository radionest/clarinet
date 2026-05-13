// Unit tests for pure helpers in `components/workflow_graph.gleam`.
// SVG rendering is not tested here — gleeunit doesn't render — but the
// per-trigger label mapping is a flat 6-branch `case` worth pinning so
// future enum additions can't silently drop a label.

import api/workflow_models.{
  TriggerNone, TriggerOnCreated, TriggerOnDataUpdate, TriggerOnFileChange,
  TriggerOnFileUpdate, TriggerOnStatus,
}
import components/workflow_graph
import gleam/option.{None, Some}
import gleeunit
import gleeunit/should

pub fn main() {
  gleeunit.main()
}

pub fn trigger_label_on_status_with_value_test() {
  workflow_graph.trigger_label_text(TriggerOnStatus, Some("finished"))
  |> should.equal("on status: finished")
}

pub fn trigger_label_on_status_without_value_test() {
  workflow_graph.trigger_label_text(TriggerOnStatus, None)
  |> should.equal("on any status")
}

pub fn trigger_label_on_data_update_test() {
  workflow_graph.trigger_label_text(TriggerOnDataUpdate, None)
  |> should.equal("on data update")
}

pub fn trigger_label_on_file_change_test() {
  workflow_graph.trigger_label_text(TriggerOnFileChange, None)
  |> should.equal("on file change")
}

pub fn trigger_label_on_file_update_test() {
  workflow_graph.trigger_label_text(TriggerOnFileUpdate, None)
  |> should.equal("on file update")
}

pub fn trigger_label_on_created_test() {
  workflow_graph.trigger_label_text(TriggerOnCreated, None)
  |> should.equal("on created")
}

pub fn trigger_label_none_returns_empty_test() {
  // TriggerNone → empty string ⇒ `edge_labels` suppresses the row entirely.
  workflow_graph.trigger_label_text(TriggerNone, None)
  |> should.equal("")
}
