"""AST enforcement for S2P's domain-bound graph facade contract."""

from __future__ import annotations

import ast
from pathlib import Path


DECISION_METHODS = frozenset(
    {
        "get_decision",
        "get_all_decisions",
        "get_verified_decisions",
        "get_decisions",
        "count_verified",
        "count_verified_decisions",
        "count_correct",
        "count_decisions",
        "count_recommended_action",
        "get_decision_links",
        "query_context",
    }
)

ALLOWED_RELATIVE_PATHS = frozenset(
    {
        Path("graph/s2p_graph_reader.py"),
        Path("main.py"),
    }
)


def _method_name(node: ast.AST) -> str | None:
    """Return a Decision method name represented by an AST node."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in DECISION_METHODS:
            return node.value
    return None


def _type_error_handler(handler: ast.ExceptHandler) -> bool:
    """Whether an except handler catches TypeError directly or in a tuple."""
    exception_type = handler.type
    if isinstance(exception_type, ast.Name):
        return exception_type.id == "TypeError"
    return isinstance(exception_type, ast.Tuple) and any(
        isinstance(item, ast.Name) and item.id == "TypeError"
        for item in exception_type.elts
    )


def _decision_calls(nodes: list[ast.stmt]) -> list[ast.Call]:
    """Find calls to Decision methods in a try block's body."""
    calls: list[ast.Call] = []
    for root in nodes:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Attribute) and function.attr in DECISION_METHODS:
                calls.append(node)
            elif isinstance(function, ast.Name) and function.id in DECISION_METHODS:
                calls.append(node)
    return calls


class _Rule72Visitor(ast.NodeVisitor):
    def __init__(self, relative_path: Path) -> None:
        self.relative_path = relative_path
        self.violations: list[str] = []

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if isinstance(function, ast.Name) and function.id in {"getattr", "hasattr"}:
            if len(node.args) >= 2:
                method = _method_name(node.args[1])
                if method is not None:
                    self.violations.append(
                        f"{self.relative_path}:{node.lineno}: "
                        f"{function.id}(..., {method!r})"
                    )
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        calls = _decision_calls(node.body)
        if calls:
            for handler in node.handlers:
                if _type_error_handler(handler):
                    self.violations.append(
                        f"{self.relative_path}:{handler.lineno}: "
                        "except TypeError around Decision method call"
                    )
        self.generic_visit(node)


def _production_violations() -> list[str]:
    app_root = Path(__file__).resolve().parents[1] / "app"
    violations: list[str] = []
    for path in sorted(app_root.rglob("*.py")):
        relative_path = path.relative_to(app_root).as_posix()
        if Path(relative_path) in ALLOWED_RELATIVE_PATHS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            violations.append(f"{relative_path}:0: unable to parse production file: {exc}")
            continue
        visitor = _Rule72Visitor(Path(relative_path))
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def test_s2p_production_uses_domain_bound_graph_facade() -> None:
    violations = _production_violations()
    assert not violations, "Rule #72 violations:\n" + "\n".join(violations)
