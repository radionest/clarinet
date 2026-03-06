"""DAG validation utilities for parent-child relationships.

Provides cycle detection for directed graphs represented as
parent-edge mappings (e.g. RecordType parent_type_name).
"""


def detect_cycle(edges: dict[str, str | None]) -> list[str] | None:
    """DFS cycle detection on a parent-edge graph.

    Args:
        edges: Mapping of node name to parent name (None = no parent).

    Returns:
        Cycle path (e.g. ``['A', 'B', 'C', 'A']``) if a cycle exists,
        or ``None`` if the graph is a valid DAG.
    """
    visited: set[str] = set()
    in_stack: set[str] = set()

    def _dfs(node: str, path: list[str]) -> list[str] | None:
        if node in in_stack:
            # Found cycle — extract the cycle path
            cycle_start = path.index(node)
            return [*path[cycle_start:], node]
        if node in visited:
            return None

        visited.add(node)
        in_stack.add(node)
        path.append(node)

        parent = edges.get(node)
        if parent is not None:
            result = _dfs(parent, path)
            if result is not None:
                return result

        path.pop()
        in_stack.remove(node)
        return None

    for node in edges:
        if node not in visited:
            result = _dfs(node, [])
            if result is not None:
                return result

    return None
