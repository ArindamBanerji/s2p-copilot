"""Probe the S2P graph boundary and prove cross-domain reads are empty."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable


_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _rows_are_s2p(rows: list[dict[str, Any]]) -> bool:
    return all(row.get("domain") == "s2p" for row in rows)


def evaluate_store(store: Any) -> dict[str, Any]:
    """Run the probe against a GraphStore-like object.

    The unfiltered call is deliberately attempted when the store supports it;
    a store that requires an explicit domain is still safe because the S2P
    reader path is checked separately by ``evaluate_reader``.
    """
    s2p_rows = list(store.get_all_decisions("s2p"))
    trading_rows = list(store.get_all_decisions("trading"))
    try:
        unfiltered_rows = list(store.get_all_decisions())
        unfiltered_error: str | None = None
    except TypeError:
        unfiltered_rows = []
        unfiltered_error = "store requires an explicit domain"
    return {
        "s2p_count": len(s2p_rows),
        "trading_count": len(trading_rows),
        "unfiltered_count": len(unfiltered_rows),
        "s2p_rows_only": _rows_are_s2p(s2p_rows),
        "unfiltered_rows_only": _rows_are_s2p(unfiltered_rows),
        "unfiltered_error": unfiltered_error,
        "isolated": (
            _rows_are_s2p(s2p_rows)
            and not trading_rows
            and unfiltered_error is None
            and _rows_are_s2p(unfiltered_rows)
        ),
    }


def evaluate_reader(reader: Any) -> dict[str, Any]:
    """Check every canonical S2P reader collection read that returns rows."""
    checks: dict[str, bool] = {}
    for name, call in (
        ("get_decisions", lambda: reader.get_decisions()),
        ("get_all_decisions", reader.get_all_decisions),
        ("get_verified_decisions", reader.get_verified_decisions),
        ("get_decision_links", reader.get_decision_links),
    ):
        rows = list(call())
        checks[name] = _rows_are_s2p(rows)
    return {"reader_checks": checks, "reader_isolated": all(checks.values())}


def build_report(store: Any, reader_factory: Callable[[Any], Any]) -> dict[str, Any]:
    store_result = evaluate_store(store)
    reader_result = evaluate_reader(reader_factory(store))
    return {
        "domain": "s2p",
        "isolated": bool(store_result["isolated"] and reader_result["reader_isolated"]),
        "counts": store_result,
        "reader": reader_result,
    }


def _live_store() -> tuple[Any, Callable[[Any], Any]]:
    from copilot_sdk.graph.factory import create_graph_store
    from app.graph.s2p_graph_reader import S2PGraphReader

    dsn = os.environ["GRAPH_DSN"]
    graph_name = os.environ.get("GRAPH_NAME", "protocol_v2_test_e2e")
    test_mode = os.environ.get("S2P_ACTIVE_AGE_TEST_MODE", "0") == "1"
    test_mode = test_mode or graph_name.startswith("protocol_v2_test")
    store = create_graph_store(
        backend="age",
        domain="s2p",
        dsn=dsn,
        graph_name=graph_name,
        test_mode=test_mode,
    )
    return store, lambda value: S2PGraphReader(value, domain="s2p")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    if not os.environ.get("GRAPH_DSN", "").strip():
        print(json.dumps({"domain": "s2p", "skipped": True, "reason": "GRAPH_DSN is not set"}, indent=2))
        return 0
    try:
        store, reader_factory = _live_store()
        report = build_report(store, reader_factory)
    except Exception as exc:
        report = {"domain": "s2p", "isolated": False, "error": str(exc)}
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("isolated") is True else 1


if __name__ == "__main__":
    sys.exit(main())
