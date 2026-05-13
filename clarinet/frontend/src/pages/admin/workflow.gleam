// Admin workflow schema visualization (route: `/admin/workflow`).
//
// Read-only project-wide graph. No `record_id`, so edges show only
// "potential" firings — no dry-run/fire here (that lives in
// `pages/records/execute.gleam` for instance-mode).

import api/types.{type ApiError, AuthError}
import api/workflow as wf_api
import api/workflow_models.{type WorkflowGraph, type WorkflowNode}
import components/workflow_graph as wf_renderer
import gleam/javascript/promise
import gleam/list
import gleam/option.{type Option, None, Some}
import gleam/set.{type Set}
import lustre/attribute
import lustre/effect.{type Effect}
import lustre/element.{type Element}
import lustre/element/html
import lustre/event
import shared.{type OutMsg, type Shared}
import utils/load_status.{type LoadStatus}

// --- Model ---

pub type Model {
  Model(
    graph: Option(WorkflowGraph),
    load_status: LoadStatus,
    view: wf_renderer.ViewTransform,
    expanded_pipelines: Set(String),
    selected_node: Option(String),
    selected_edge: Option(String),
    service_disabled: Bool,
    /// Generation counter — incremented on every load_graph_effect
    /// dispatch. Late responses with a stale id are dropped to avoid
    /// flicker when rapid TogglePipeline clicks queue multiple requests.
    request_id: Int,
  )
}

// --- Msg ---

pub type Msg {
  GraphLoaded(request_id: Int, result: Result(WorkflowGraph, ApiError))
  RetryLoad
  TogglePipeline(String)
  PanZoom(wf_renderer.ViewTransform)
  NodeClicked(String)
  EdgeClicked(String)
  ClearSelection
}

// --- Init ---

pub fn init(_shared: Shared) -> #(Model, Effect(Msg), List(OutMsg)) {
  let initial_request_id = 1
  let model =
    Model(
      graph: None,
      load_status: load_status.Loading,
      view: wf_renderer.identity(),
      expanded_pipelines: set.new(),
      selected_node: None,
      selected_edge: None,
      service_disabled: False,
      request_id: initial_request_id,
    )
  #(model, load_graph_effect(initial_request_id, set.new()), [])
}

fn load_graph_effect(request_id: Int, expanded: Set(String)) -> Effect(Msg) {
  use dispatch <- effect.from
  wf_api.get_graph(None, set.to_list(expanded), wf_api.Schema)
  |> promise.tap(fn(result) { dispatch(GraphLoaded(request_id, result)) })
  Nil
}

// --- Update ---

pub fn update(
  model: Model,
  msg: Msg,
  _shared: Shared,
) -> #(Model, Effect(Msg), List(OutMsg)) {
  case msg {
    GraphLoaded(id, _) if id != model.request_id ->
      // Stale response from a superseded request — ignore.
      #(model, effect.none(), [])

    GraphLoaded(_, Ok(graph)) -> #(
      Model(
        ..model,
        graph: Some(graph),
        load_status: load_status.Loaded,
        service_disabled: False,
      ),
      effect.none(),
      [],
    )

    GraphLoaded(_, Error(err)) -> {
      let #(load_state, service_disabled) = wf_api.classify_load_error(err)
      #(
        Model(..model, load_status: load_state, service_disabled: service_disabled),
        effect.none(),
        handle_error(err),
      )
    }

    RetryLoad -> {
      let next_id = model.request_id + 1
      #(
        Model(
          ..model,
          load_status: load_status.Loading,
          service_disabled: False,
          request_id: next_id,
        ),
        load_graph_effect(next_id, model.expanded_pipelines),
        [],
      )
    }

    TogglePipeline(name) -> {
      let new_expanded = case set.contains(model.expanded_pipelines, name) {
        True -> set.delete(model.expanded_pipelines, name)
        False -> set.insert(model.expanded_pipelines, name)
      }
      let next_id = model.request_id + 1
      #(
        Model(
          ..model,
          expanded_pipelines: new_expanded,
          load_status: load_status.Loading,
          request_id: next_id,
        ),
        load_graph_effect(next_id, new_expanded),
        [],
      )
    }

    PanZoom(v) -> #(Model(..model, view: v), effect.none(), [])

    NodeClicked(node_id) -> {
      let toggle_pipeline_eff = case node_lookup(model.graph, node_id) {
        Some(node) ->
          case workflow_models.pipeline_name_from_id(node.id) {
            Some(name) -> dispatch_msg(TogglePipeline(name))
            None -> effect.none()
          }
        None -> effect.none()
      }
      #(
        Model(..model, selected_node: Some(node_id), selected_edge: None),
        toggle_pipeline_eff,
        [],
      )
    }

    EdgeClicked(edge_id) -> #(
      Model(..model, selected_edge: Some(edge_id), selected_node: None),
      effect.none(),
      [],
    )

    ClearSelection -> #(
      Model(..model, selected_node: None, selected_edge: None),
      effect.none(),
      [],
    )
  }
}

/// Graph load uses page-local `LoadStatus`, so we must NOT emit
/// `shared.SetLoading` (no matching `SetLoading(True)` was issued; emitting
/// `SetLoading(False)` here would clobber an unrelated in-flight global op).
/// The error message itself is surfaced via `load_status.Failed(...)` and
/// rendered by `error_view`, so no toast is needed.
fn handle_error(err: ApiError) -> List(OutMsg) {
  case err {
    AuthError(_) -> [shared.Logout]
    _ -> []
  }
}

fn dispatch_msg(msg: Msg) -> Effect(Msg) {
  use dispatch <- effect.from
  dispatch(msg)
  Nil
}

fn node_lookup(
  graph: Option(WorkflowGraph),
  node_id: String,
) -> Option(WorkflowNode) {
  case graph {
    Some(g) ->
      list.find(g.nodes, fn(n) { n.id == node_id })
      |> option.from_result
    None -> None
  }
}

// --- View ---

pub fn view(model: Model, _shared: Shared) -> Element(Msg) {
  html.div([attribute.class("workflow-page container")], [
    html.div([attribute.class("page-header")], [
      html.h1([], [html.text("Workflow")]),
      html.p([attribute.class("text-muted")], [
        html.text(
          "Project-wide schema graph. Drag to pan, scroll to zoom. "
          <> "Click a pipeline node to expand it.",
        ),
      ]),
    ]),
    load_status.render(
      model.load_status,
      fn() { loading_view() },
      fn() { graph_layout(model) },
      fn(msg) { error_view(msg, model.service_disabled) },
    ),
  ])
}

fn graph_layout(model: Model) -> Element(Msg) {
  case model.graph {
    Some(graph) ->
      html.div([attribute.class("workflow-layout")], [
        html.div([attribute.class("workflow-canvas")], [
          wf_renderer.render(
            graph,
            model.view,
            model.selected_node,
            model.selected_edge,
            wf_renderer.Handlers(
              on_node_click: NodeClicked,
              on_edge_click: EdgeClicked,
              on_pan_zoom: PanZoom,
            ),
          ),
        ]),
        side_panel(model, graph),
      ])
    None -> loading_view()
  }
}

fn side_panel(model: Model, graph: WorkflowGraph) -> Element(Msg) {
  let body = case model.selected_node, model.selected_edge {
    Some(node_id), _ ->
      case list.find(graph.nodes, fn(n) { n.id == node_id }) {
        Ok(node) -> wf_renderer.node_panel(node, wf_renderer.expand_hint(node))
        Error(_) -> wf_renderer.empty_panel()
      }
    _, Some(edge_id) ->
      case list.find(graph.edges, fn(e) { e.id == edge_id }) {
        Ok(edge) -> wf_renderer.edge_panel(edge, dry_run_hint())
        Error(_) -> wf_renderer.empty_panel()
      }
    _, _ -> wf_renderer.empty_panel()
  }
  html.aside([attribute.class("workflow-side-panel")], [
    html.div([attribute.class("workflow-side-panel-header")], [
      html.h3([], [html.text("Details")]),
      case model.selected_node, model.selected_edge {
        Some(_), _ | _, Some(_) ->
          html.button(
            [
              attribute.class("btn btn-sm btn-secondary"),
              event.on_click(ClearSelection),
            ],
            [html.text("Close")],
          )
        _, _ -> element.none()
      },
    ]),
    body,
  ])
}

fn dry_run_hint() -> Element(Msg) {
  html.p([attribute.class("text-muted")], [
    html.text(
      "Open a specific record to dry-run or fire this trigger from the "
      <> "record execution page.",
    ),
  ])
}

fn loading_view() -> Element(Msg) {
  html.div([attribute.class("loading")], [
    html.p([], [html.text("Loading workflow graph...")]),
  ])
}

fn error_view(message: String, service_disabled: Bool) -> Element(Msg) {
  html.div([attribute.class("error-container")], [
    html.p([attribute.class("error-message")], [html.text(message)]),
    case service_disabled {
      True -> element.none()
      False ->
        html.button(
          [attribute.class("btn btn-primary"), event.on_click(RetryLoad)],
          [html.text("Retry")],
        )
    },
  ])
}
