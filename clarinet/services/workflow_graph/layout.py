"""Compute (x, y) positions for a :class:`WorkflowGraph`.

Topological layered layout via Kahn's algorithm. Within each layer nodes are
ordered by id for stability. Cycles are tolerated — nodes unreachable from
sources are assigned a layer one greater than their highest predecessor.

This is intentionally simple. The Lustre frontend can still pan/zoom freely
on top of these positions; user-driven drag/reflow is left for a future
iteration (would just override these positions client-side).
"""

from __future__ import annotations

from collections import deque

from .models import Position, WorkflowGraph

DEFAULT_X_SPACING = 260.0
DEFAULT_Y_SPACING = 90.0
DEFAULT_PADDING = 40.0
DEFAULT_NODE_WIDTH = 200.0
DEFAULT_NODE_HEIGHT = 60.0


def apply_layout(
    graph: WorkflowGraph,
    *,
    x_spacing: float = DEFAULT_X_SPACING,
    y_spacing: float = DEFAULT_Y_SPACING,
    padding: float = DEFAULT_PADDING,
    node_width: float = DEFAULT_NODE_WIDTH,
    node_height: float = DEFAULT_NODE_HEIGHT,
) -> WorkflowGraph:
    """Mutate ``graph`` in place: assign ``node.position`` and graph width/height."""
    layers = _compute_layers(graph)

    nodes_by_layer: dict[int, list[str]] = {}
    for node in graph.nodes:
        layer = layers.get(node.id, 0)
        nodes_by_layer.setdefault(layer, []).append(node.id)

    node_map = {n.id: n for n in graph.nodes}
    max_x = 0.0
    max_y = 0.0

    for layer_idx in sorted(nodes_by_layer):
        layer_node_ids = sorted(nodes_by_layer[layer_idx])
        for row_idx, node_id in enumerate(layer_node_ids):
            node = node_map[node_id]
            x = padding + layer_idx * x_spacing
            y = padding + row_idx * y_spacing
            node.position = Position(x=x, y=y)
            max_x = max(max_x, x + node_width)
            max_y = max(max_y, y + node_height)

    graph.width = max_x + padding
    graph.height = max_y + padding
    return graph


def _compute_layers(graph: WorkflowGraph) -> dict[str, int]:
    incoming: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    outgoing: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for edge in graph.edges:
        if edge.from_node not in incoming or edge.to_node not in incoming:
            continue
        incoming[edge.to_node].append(edge.from_node)
        outgoing[edge.from_node].append(edge.to_node)

    in_degree = {nid: len(preds) for nid, preds in incoming.items()}
    layer = {n.id: 0 for n in graph.nodes}

    queue: deque[str] = deque(nid for nid, d in in_degree.items() if d == 0)
    visited: set[str] = set()

    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        for neighbour in outgoing[node_id]:
            if layer[neighbour] < layer[node_id] + 1:
                layer[neighbour] = layer[node_id] + 1
            in_degree[neighbour] -= 1
            if in_degree[neighbour] == 0:
                queue.append(neighbour)

    # Cycles: place unvisited nodes one layer below their deepest predecessor
    for node in graph.nodes:
        if node.id in visited:
            continue
        preds = incoming[node.id]
        if not preds:
            continue
        layer[node.id] = max(layer.get(p, 0) for p in preds) + 1

    return layer


__all__ = ["apply_layout"]
