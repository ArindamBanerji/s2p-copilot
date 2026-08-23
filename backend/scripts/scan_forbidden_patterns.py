"""Scan S2P application code for forbidden graph and safety patterns.

The scanner is intentionally independent of the running application.  Its
``scan_tree`` function is also used by tests with a temporary source tree.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    pattern: str
    detail: str


_IMPORT_RE = re.compile(r"^\s*(?:from\s+neo4j\b|import\s+neo4j\b)", re.IGNORECASE)
_DSN_RE = re.compile(r"aura|bolt://|neo4j://|neo4j\+s://", re.IGNORECASE)
_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:\s*$")
_TYPE_IGNORE_RE = re.compile(r"#\s*type:\s*ignore\b", re.IGNORECASE)
_EMPTY_DOMAIN_RE = re.compile(
    r"(?:domain\s*=\s*['\"]\s*['\"]|domain\s+or\s+['\"]\s*['\"]|"
    r"\.get\(\s*['\"]domain['\"]\s*,\s*['\"]\s*['\"]\s*\))",
    re.IGNORECASE,
)
_MATCH_RE = re.compile(r"\bMATCH\b", re.IGNORECASE)


def _violation(path: Path, line: int, pattern: str, detail: str) -> Violation:
    return Violation(str(path), line, pattern, detail.strip()[:240])


def _cypher_text(node: ast.JoinedStr | ast.Constant) -> str | None:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    return "".join(
        item.value if isinstance(item, ast.Constant) and isinstance(item.value, str) else "{...}"
        for item in node.values
    )


def _is_cypher(text: str) -> bool:
    return bool(_MATCH_RE.search(text) and re.search(r"\b(?:RETURN|WHERE|CREATE|MERGE)\b", text, re.IGNORECASE))


def _joined_cypher_violations(path: Path, source: str) -> Iterable[Violation]:
    """Find interpolated Cypher whose domain predicate is not source-bound.

    S2P's reader uses f-strings for escaped scalar values, but its domain is
    explicitly bound to ``self.domain`` and guarded by a domain predicate.
    That is accepted here; a query interpolating a domain value without that
    binding is not.
    """
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return
    lines = source.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.JoinedStr, ast.Constant)):
            continue
        text = _cypher_text(node)
        if text is None or not _is_cypher(text):
            continue
        has_domain_predicate = bool(re.search(r"\bWHERE\b.*\bdomain\b", text, re.IGNORECASE | re.DOTALL))
        if not has_domain_predicate:
            snippet = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            pattern = "f-string-cypher-domain" if isinstance(node, ast.JoinedStr) else "unscoped-match"
            yield _violation(path, node.lineno, pattern, snippet)


def scan_file(path: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    found: list[Violation] = []
    for index, line in enumerate(lines, start=1):
        if _IMPORT_RE.search(line):
            found.append(_violation(path, index, "neo4j-import", line))
        if _DSN_RE.search(line):
            found.append(_violation(path, index, "aura-or-bolt-dsn", line))
        if "InMemoryGraphStore" in line and path.name != "s2p_preview.py":
            found.append(_violation(path, index, "in-memory-production-store", line))
        if _BARE_EXCEPT_RE.match(line):
            found.append(_violation(path, index, "bare-except", line))
        if _TYPE_IGNORE_RE.search(line):
            found.append(_violation(path, index, "type-ignore", line))
        if _EMPTY_DOMAIN_RE.search(line):
            found.append(_violation(path, index, "empty-domain-substitution", line))
    found.extend(_joined_cypher_violations(path, source))
    return found


def scan_tree(app_root: Path) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(app_root.rglob("*.py")):
        if "__pycache__" not in path.parts:
            violations.extend(scan_file(path))
    return violations


def build_report(app_root: Path) -> dict[str, object]:
    violations = scan_tree(app_root)
    return {
        "root": str(app_root),
        "violations": [asdict(item) for item in violations],
        "total": len(violations),
        "clean": not violations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "app")
    args = parser.parse_args(argv)
    report = build_report(args.root.resolve())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
