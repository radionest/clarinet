// Stateless SVG renderer for `WorkflowGraph`.
//
// Pan & zoom is implemented by mutating a single root `<g transform>`.
// All drag/zoom state lives in the parent page's Model (we only emit a new
// `ViewTransform` via `Handlers.on_pan_zoom`). This keeps the component
// pure and free of internal state.

import api/workflow_models.{
  type EdgeKind, type NodeKind, type WorkflowEdge, type WorkflowGraph,
  type WorkflowNode, CallFunctionEdge, CallFunctionNode, CreateRecordEdge,
  EntityNode, FileNode, InvalidateEdge, PipelineDispatchEdge, PipelineNode,
  PipelineStepChainEdge, PipelineStepNode, RecordTypeNode, UpdateRecordEdge,
}
import gleam/dict.{type Dict}
import gleam/dynamic/decode
import gleam/float
import gleam/int
import gleam/list
import gleam/option.{type Option, None, Some}
import lustre/attribute
import lustre/element.{type Element}
import lustre/element/html
import lustre/element/svg
import lustre/event

pub type ViewTransform {
  ViewTransform(tx: Float, ty: Float, scale: Float)
}

pub type Handlers(msg) {
  Handlers(
    on_node_click: fn(String) -> msg,
    on_edge_click: fn(String) -> msg,
    on_pan_zoom: fn(ViewTransform) -> msg,
  )
}

pub fn identity() -> ViewTransform {
  ViewTransform(tx: 0.0, ty: 0.0, scale: 1.0)
}

// Synced with backend defaults in `clarinet/services/workflow_graph/layout.py`.
const node_width: Float = 200.0

const node_height: Float = 60.0

const min_scale: Float = 0.2

const max_scale: Float = 5.0

const wheel_factor_in: Float = 1.1

const wheel_factor_out: Float = 0.9

pub fn render(
  graph: WorkflowGraph,
  view: ViewTransform,
  selected_node_id: Option(String),
  selected_edge_id: Option(String),
  handlers: Handlers(msg),
) -> Element(msg) {
  let nodes_by_id =
    list.fold(graph.nodes, dict.new(), fn(acc, n) {
      dict.insert(acc, n.id, n)
    })
  let viewbox =
    "0 0 "
    <> float.to_string(graph.width)
    <> " "
    <> float.to_string(graph.height)
  svg.svg(
    [
      attribute.class("workflow-svg"),
      attribute.attribute("viewBox", viewbox),
      attribute.attribute("preserveAspectRatio", "xMidYMid meet"),
      // Ctrl+wheel zooms (with preventDefault to suppress browser zoom);
      // plain wheel falls through so the user can scroll the surrounding
      // page past the canvas.
      event.advanced(
        "wheel",
        wheel_handler(graph.width, graph.height, view, handlers.on_pan_zoom),
      ),
      event.on("mousemove", drag_decoder(view, handlers.on_pan_zoom)),
    ],
    [
      defs_block(),
      svg.g([attribute.attribute("transform", transform_attr(view))], [
        svg.g(
          [attribute.class("workflow-edges")],
          list.map(graph.edges, fn(e) {
            edge_view(e, nodes_by_id, selected_edge_id, handlers.on_edge_click)
          }),
        ),
        svg.g(
          [attribute.class("workflow-nodes")],
          list.map(graph.nodes, fn(n) {
            node_view(n, selected_node_id, handlers.on_node_click)
          }),
        ),
      ]),
    ],
  )
}

fn defs_block() -> Element(msg) {
  svg.defs([], [
    svg.marker(
      [
        attribute.id("workflow-arrow"),
        attribute.attribute("viewBox", "0 -5 10 10"),
        attribute.attribute("refX", "9"),
        attribute.attribute("refY", "0"),
        attribute.attribute("markerWidth", "8"),
        attribute.attribute("markerHeight", "8"),
        attribute.attribute("orient", "auto"),
      ],
      [
        svg.path([
          attribute.attribute("d", "M0,-5L10,0L0,5Z"),
          attribute.class("workflow-edge-arrow"),
        ]),
      ],
    ),
  ])
}

fn node_view(
  node: WorkflowNode,
  selected_id: Option(String),
  on_click: fn(String) -> msg,
) -> Element(msg) {
  let is_selected = selected_id == Some(node.id)
  let base_class = "workflow-node " <> node_kind_class(node.kind)
  let cls = case is_selected, node.expanded {
    True, _ -> base_class <> " workflow-node--selected"
    _, True -> base_class <> " workflow-node--expanded"
    _, _ -> base_class
  }
  let indicator = case node.expandable, node.expanded {
    True, True -> "▾ "
    True, False -> "▸ "
    _, _ -> ""
  }
  let label = indicator <> node.label
  let label_x = node.position.x +. node_width /. 2.0
  let label_y = node.position.y +. node_height /. 2.0 +. 5.0
  svg.g([attribute.class(cls), event.on_click(on_click(node.id))], [
    svg.title([], [element.text(node.id)]),
    svg.rect([
      attribute.attribute("x", float.to_string(node.position.x)),
      attribute.attribute("y", float.to_string(node.position.y)),
      attribute.attribute("width", float.to_string(node_width)),
      attribute.attribute("height", float.to_string(node_height)),
      attribute.attribute("rx", "6"),
      attribute.attribute("ry", "6"),
    ]),
    svg.text(
      [
        attribute.attribute("x", float.to_string(label_x)),
        attribute.attribute("y", float.to_string(label_y)),
        attribute.attribute("text-anchor", "middle"),
      ],
      label,
    ),
  ])
}

fn edge_view(
  edge: WorkflowEdge,
  nodes_by_id: Dict(String, WorkflowNode),
  selected_id: Option(String),
  on_click: fn(String) -> msg,
) -> Element(msg) {
  case dict.get(nodes_by_id, edge.from_node), dict.get(nodes_by_id, edge.to_node)
  {
    Ok(from), Ok(to) -> render_edge(edge, from, to, selected_id, on_click)
    _, _ -> element.none()
  }
}

fn render_edge(
  edge: WorkflowEdge,
  from: WorkflowNode,
  to: WorkflowNode,
  selected_id: Option(String),
  on_click: fn(String) -> msg,
) -> Element(msg) {
  let is_selected = selected_id == Some(edge.id)
  let is_fired = case edge.firings {
    [] -> False
    _ -> True
  }
  let base = "workflow-edge " <> edge_kind_class(edge.kind)
  let with_state = case is_fired {
    True -> base <> " workflow-edge--fired"
    False -> base <> " workflow-edge--potential"
  }
  let cls = case is_selected {
    True -> with_state <> " workflow-edge--selected"
    False -> with_state
  }
  let d = edge_path(from, to)
  svg.g([attribute.class(cls), event.on_click(on_click(edge.id))], [
    svg.title([], [element.text(edge_tooltip(edge))]),
    // Invisible hit target — расширяет click-area без визуального утолщения.
    // marker-end не ставим, иначе будет двойной arrowhead.
    svg.path([
      attribute.attribute("d", d),
      attribute.class("workflow-edge-hitbox"),
    ]),
    svg.path([
      attribute.attribute("d", d),
      attribute.attribute("marker-end", "url(#workflow-arrow)"),
    ]),
  ])
}

// Right-edge of `from` to left-edge of `to`. Straight line; arrow via marker.
fn edge_path(from: WorkflowNode, to: WorkflowNode) -> String {
  let x1 = from.position.x +. node_width
  let y1 = from.position.y +. node_height /. 2.0
  let x2 = to.position.x
  let y2 = to.position.y +. node_height /. 2.0
  "M"
  <> float.to_string(x1)
  <> " "
  <> float.to_string(y1)
  <> " L"
  <> float.to_string(x2)
  <> " "
  <> float.to_string(y2)
}

fn edge_tooltip(edge: WorkflowEdge) -> String {
  let label = case edge.label {
    Some(l) -> l
    None -> edge.id
  }
  case edge.trigger_value {
    Some(v) -> label <> " — " <> v
    None -> label
  }
}

fn node_kind_class(kind: NodeKind) -> String {
  case kind {
    RecordTypeNode -> "workflow-node--record-type"
    EntityNode -> "workflow-node--entity"
    FileNode -> "workflow-node--file"
    PipelineNode -> "workflow-node--pipeline"
    PipelineStepNode -> "workflow-node--pipeline-step"
    CallFunctionNode -> "workflow-node--call-function"
  }
}

fn edge_kind_class(kind: EdgeKind) -> String {
  case kind {
    CreateRecordEdge -> "workflow-edge--create-record"
    UpdateRecordEdge -> "workflow-edge--update-record"
    InvalidateEdge -> "workflow-edge--invalidate"
    CallFunctionEdge -> "workflow-edge--call-function"
    PipelineDispatchEdge -> "workflow-edge--pipeline-dispatch"
    PipelineStepChainEdge -> "workflow-edge--pipeline-step-chain"
  }
}

fn transform_attr(view: ViewTransform) -> String {
  "translate("
  <> float.to_string(view.tx)
  <> " "
  <> float.to_string(view.ty)
  <> ") scale("
  <> float.to_string(view.scale)
  <> ")"
}

// --- Pan / zoom decoders ---

// Zoom around the viewBox center so the canvas mid-point stays fixed,
// instead of pivoting around the origin (where the graph "jumps sideways"
// as scale grows). True cursor-centered zoom needs `getBoundingClientRect`
// to map clientX/Y into viewBox space, which we'd have to add via FFI —
// deferred.
//
// Gating on `ctrlKey` lets the canvas coexist with normal page scrolling:
// plain wheel → no zoom + no preventDefault → page scrolls;
// Ctrl+wheel → zoom + preventDefault (to suppress the browser zoom that
// Ctrl+wheel would otherwise trigger).
fn wheel_handler(
  graph_width: Float,
  graph_height: Float,
  view: ViewTransform,
  on_pan_zoom: fn(ViewTransform) -> msg,
) -> decode.Decoder(event.Handler(msg)) {
  use ctrl_key <- decode.field("ctrlKey", decode.bool)
  use <- guard_decoder(ctrl_key, on_pan_zoom(view))
  use delta_y <- decode.field("deltaY", decode.float)
  let factor = case delta_y >. 0.0 {
    True -> wheel_factor_out
    False -> wheel_factor_in
  }
  let new_scale = clamp(view.scale *. factor, min_scale, max_scale)
  let ratio = new_scale /. view.scale
  let pivot_x = graph_width /. 2.0
  let pivot_y = graph_height /. 2.0
  let new_tx = pivot_x -. { pivot_x -. view.tx } *. ratio
  let new_ty = pivot_y -. { pivot_y -. view.ty } *. ratio
  decode.success(event.handler(
    dispatch: on_pan_zoom(
      ViewTransform(tx: new_tx, ty: new_ty, scale: new_scale),
    ),
    prevent_default: True,
    stop_propagation: False,
  ))
}

// `decode.failure` short-circuits dispatch when the gate is False — without
// it we'd compute a zoom transform even on plain wheels and rely on
// Lustre to suppress dispatch separately.
fn guard_decoder(
  cond: Bool,
  placeholder: msg,
  body: fn() -> decode.Decoder(event.Handler(msg)),
) -> decode.Decoder(event.Handler(msg)) {
  case cond {
    True -> body()
    False ->
      decode.failure(
        event.handler(
          dispatch: placeholder,
          prevent_default: False,
          stop_propagation: False,
        ),
        "gate closed",
      )
  }
}

// `MouseEvent.buttons` is a bitmask — LSB = primary button held.
// `decode.failure` makes Lustre skip dispatch when the user isn't dragging,
// so we don't re-render on every idle hover.
fn drag_decoder(
  view: ViewTransform,
  on_pan_zoom: fn(ViewTransform) -> msg,
) -> decode.Decoder(msg) {
  use buttons <- decode.field("buttons", decode.int)
  case int.bitwise_and(buttons, 1) {
    1 -> {
      use mx <- decode.field("movementX", decode.float)
      use my <- decode.field("movementY", decode.float)
      decode.success(
        on_pan_zoom(
          ViewTransform(..view, tx: view.tx +. mx, ty: view.ty +. my),
        ),
      )
    }
    _ -> decode.failure(on_pan_zoom(view), "no drag")
  }
}

fn clamp(value: Float, lo: Float, hi: Float) -> Float {
  case value <. lo, value >. hi {
    True, _ -> lo
    _, True -> hi
    _, _ -> value
  }
}

// --- Side-panel renderers (shared between admin and instance pages) ---
//
// Pages pass page-specific hints via `footer` (e.g. "click to expand" on
// admin, "click again to dry-run" on instance). Pure HTML — no SVG, no
// click handlers; the footer carries any page-specific buttons.

pub fn node_panel(node: WorkflowNode, footer: Element(msg)) -> Element(msg) {
  html.div([attribute.class("workflow-side-panel-body")], [
    html.dl([], [
      html.dt([], [html.text("Type")]),
      html.dd([], [html.text(workflow_models.node_kind_label(node.kind))]),
      html.dt([], [html.text("Label")]),
      html.dd([], [html.text(node.label)]),
      html.dt([], [html.text("ID")]),
      html.dd([], [html.text(node.id)]),
    ]),
    footer,
  ])
}

pub fn edge_panel(edge: WorkflowEdge, footer: Element(msg)) -> Element(msg) {
  let firings_text = case edge.firings {
    [] -> "0 (potential edge)"
    fs -> int.to_string(list.length(fs)) <> " (fired)"
  }
  html.div([attribute.class("workflow-side-panel-body")], [
    html.dl(
      [],
      list.flatten([
        [
          html.dt([], [html.text("Kind")]),
          html.dd([], [html.text(workflow_models.edge_kind_label(edge.kind))]),
          html.dt([], [html.text("Trigger")]),
          html.dd([], [
            html.text(workflow_models.trigger_kind_label(edge.trigger_kind)),
          ]),
        ],
        optional_dl_row("Trigger value", edge.trigger_value),
        optional_dl_row("Condition", edge.condition_summary),
        optional_dl_row("Label", edge.label),
        [
          html.dt([], [html.text("Firings")]),
          html.dd([], [html.text(firings_text)]),
        ],
      ]),
    ),
    footer,
  ])
}

pub fn empty_panel() -> Element(msg) {
  html.div([attribute.class("workflow-side-panel-body")], [
    html.p([attribute.class("text-muted")], [
      html.text("Click a node or edge to see details."),
    ]),
  ])
}

/// Footer hint for `node_panel` — explains the expand/collapse semantics
/// of pipeline nodes. Both admin schema view and per-record instance view
/// use the same wording, so it lives here.
pub fn expand_hint(node: WorkflowNode) -> Element(msg) {
  case node.expandable {
    True ->
      html.p([attribute.class("text-muted")], [
        html.text(
          "Pipeline node — click to "
          <> case node.expanded {
            True -> "collapse"
            False -> "expand"
          },
        ),
      ])
    False -> element.none()
  }
}

fn optional_dl_row(label: String, value: Option(String)) -> List(Element(msg)) {
  case value {
    Some(v) -> [
      html.dt([], [html.text(label)]),
      html.dd([], [html.text(v)]),
    ]
    None -> []
  }
}
