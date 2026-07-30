"""AST enforcement for S2P's domain-bound graph facade contract."""

from __future__ import annotations

import ast
from pathlib import Path


PROTOCOL_METHODS = frozenset(
    {
        "write_decision",
        "write_outcome",
        "get_decision",
        "get_decisions",
        "get_all_decisions",
        "get_archived_decisions",
        "get_verified_decisions",
        "count_verified",
        "count_verified_decisions",
        "count_correct",
        "count_decisions",
        "save_centroids",
        "load_latest_centroids",
        "get_centroid_checkpoints",
        "archive_old_decisions",
        "count_archived",
        "close",
        "write_entity_enrichment",
        "read_entity_enrichment",
        "list_entity_enrichments",
        "get_decision_links",
        "query_context",
        "query_similar",
        "generate_decision_id",
        "write_governed_decision",
        "write_observation",
        "append_evidence_receipt",
        "write_conservation_status",
        "write_fingerprint",
        "write_centroid_checkpoint",
        "write_evolution_event",
        "write_transfer_pattern",
        "get_transfer_patterns",
        "get_latest_conservation_statuses",
        "get_iks_trajectory",
        "link_entity",
        "archive_decisions",
        "domain_scoped_reset",
    }
)

DECISION_METHODS = PROTOCOL_METHODS | {"count_recommended_action"}

DOMAIN_REQUIRED = frozenset(
    {
        "get_decisions",
        "get_all_decisions",
        "get_verified_decisions",
        "count_verified",
        "count_verified_decisions",
        "count_correct",
        "count_decisions",
        "query_context",
    }
)

DOMAIN_POSITION = {
    "get_decisions": 0,
    "get_all_decisions": 0,
    "get_verified_decisions": 0,
    "count_verified": 0,
    "count_verified_decisions": 0,
    "count_correct": 0,
    "count_decisions": 0,
    "query_context": 2,
}

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


def _has_domain_argument(call: ast.Call, method: str) -> bool:
    if any(keyword.arg == "domain" for keyword in call.keywords):
        return True
    position = DOMAIN_POSITION.get(method)
    return position is not None and len(call.args) > position


def _raw_unscoped_decision_query(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "run_query":
        return False
    literals = [
        node.value
        for node in ast.walk(call)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    query_text = " ".join(literals)
    if "Decision" not in query_text:
        return False
    has_scope_expression = "d.domain" in query_text or any(
        isinstance(node, ast.Name) and node.id == "domain_clause"
        for node in ast.walk(call)
    )
    return not has_scope_expression


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
        if _raw_unscoped_decision_query(node):
            self.violations.append(
                f"{self.relative_path}:{node.lineno}: raw run_query for an "
                "Decision query has no domain predicate"
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
