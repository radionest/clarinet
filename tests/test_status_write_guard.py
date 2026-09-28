"""Guard: no SQL ``update(Record)…values(status=…)`` outside write_transition.

ORM writes (``record.status = …``, ``setattr``, generic field updaters) are refused
at runtime by the listener in ``clarinet/models/record.py``; this scan covers the
form that listener cannot see — a Core UPDATE built on ``update(Record)``. A values
dict built elsewhere and passed by name is not detected (review catches it);
``write_transition`` builds its dict that way, so it needs no allowlist entry.
"""

import ast
from pathlib import Path

CLARINET = Path(__file__).resolve().parents[1] / "clarinet"


def _targets_record(node: ast.expr) -> bool:
    """Whether a statement chain starts at ``update(Record)``."""
    while True:
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "update":
                return any(isinstance(a, ast.Name) and a.id == "Record" for a in node.args)
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        else:
            return False


def status_sql_writes(source: str) -> list[tuple[int, str]]:
    """(line, enclosing function) of every ``update(Record)…values(status=…)`` in ``source``."""
    found: list[tuple[int, str]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.func = "<module>"

        def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            outer, self.func = self.func, node.name
            self.generic_visit(node)
            self.func = outer

        visit_FunctionDef = _visit_func
        visit_AsyncFunctionDef = _visit_func

        def visit_Call(self, node: ast.Call) -> None:
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "values"
                and _targets_record(func.value)
            ):
                keys = [kw.arg for kw in node.keywords]
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        keys += [k.value for k in arg.keys if isinstance(k, ast.Constant)]
                if "status" in keys:
                    found.append((node.lineno, self.func))
            self.generic_visit(node)

    Visitor().visit(ast.parse(source))
    return found


def test_no_sql_update_sets_record_status():
    violations = [
        f"clarinet/{path.relative_to(CLARINET).as_posix()}:{line} in {func}()"
        for path in sorted(CLARINET.rglob("*.py"))
        for line, func in status_sql_writes(path.read_text(encoding="utf-8"))
    ]
    assert violations == [], "Record.status set by SQL outside write_transition:\n" + "\n".join(
        violations
    )


def test_guard_catches_each_sql_form():
    source = (
        "def a(rid):\n    return update(Record).where(Record.id == rid).values(status=1)\n"
        "def b():\n    return update(Record).values({'status': 2})\n"
        "def c():\n    return update(Record).values(data=3)\n"
        "def d():\n    return update(Study).values(status=4)\n"
        "def e(values):\n    return update(Record).values(values)\n"
    )
    assert [func for _, func in status_sql_writes(source)] == ["a", "b"]
