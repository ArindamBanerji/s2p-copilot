"""Run the live S2P conservation-gate demo.

The script deliberately uses the running S2P API for every score and learning
write.  It keeps only the report narrative locally; it does not implement a
second scorer or conservation gate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


INVOICE_COUNT = 32
PENALTY_RATIO = 5.0
THETA_NUMERATOR = 23.53
TENSOR_SHAPE = (5, 5, 8)
DEFAULT_CATEGORY = "format_compliance"


class LiveBackendError(RuntimeError):
    """Raised when the live backend cannot complete the demo contract."""


def _request(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except (HTTPError, URLError, TimeoutError) as exc:
        detail = ""
        if isinstance(exc, HTTPError):
            try:
                detail = exc.read().decode("utf-8")[:500]
            except OSError:
                detail = ""
        raise LiveBackendError(f"{method} {path} failed: {exc} {detail}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LiveBackendError(f"{method} {path} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise LiveBackendError(f"{method} {path} returned a non-object JSON payload")
    return value


def _status_label(payload: dict[str, Any]) -> str:
    value = payload.get("status")
    return str(value).upper() if value is not None else "UNKNOWN"


def _theta_min(alpha: float, verified: int) -> float | None:
    if alpha <= 0.0 or verified <= 0:
        return None
    return THETA_NUMERATOR / (alpha * verified)


def _invoice(category: str, index: int) -> dict[str, Any]:
    return {
        "event_id": f"E20-CONSERVATION-{index:03d}",
        "category": category,
        "amount": 1000.0 + index * 17.0,
        "supplier_id": f"E20-SUPPLIER-{index % 4:02d}",
        "supplier_name": "E20 Verified Supplier",
        "contract_id": "E20-CONTRACT-001",
        "approved_categories": [category],
        "supplier_risk_rating": 0.05,
        "historical_spend_mean": 1000.0,
        "historical_spend_std": 100.0,
        "vendor_decisions": index,
        "vendor_approvals": index,
        "match_status": 1.0,
        "amount_variance_ratio": 0.01,
        "duplicate_score": 0.01,
        "supplier_exception_history": 0.02,
        "payment_terms_impact": 0.10,
        "commodity_index_correlation": 0.90,
        "tax_regulatory_compliance": 1.0,
        "environmental_risk": 0.10,
    }


def _learn_payload(score: dict[str, Any]) -> dict[str, Any]:
    decision_id = score.get("decision_id")
    action = score.get("action")
    if not isinstance(decision_id, str) or not isinstance(action, str):
        raise LiveBackendError("score response did not contain decision_id and action")
    return {
        "decision_id": decision_id,
        "actual_action": action,
        "outcome": "confirmed",
        "context": {"demo": "E20 conservation gate", "source": "live_s2p_backend"},
    }


def run_demo(base_url: str, category: str, count: int, report_path: Path) -> dict[str, Any]:
    if count < 30:
        raise ValueError("count must be at least 30")

    initial = _request(base_url, "GET", "/api/conservation/status")
    initial_global_status = _status_label(initial)
    print(f"E20 · Live S2P backend: {base_url}")
    print(f"Category seeded with zero demo decisions: {category}")
    print("RED · no category evidence yet · auto-approve is held")

    scores: list[dict[str, Any]] = []
    confirmations: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        score = _request(base_url, "POST", "/api/s2p/score", _invoice(category, index))
        scores.append(score)
        confirmation = _request(base_url, "POST", "/api/learn", _learn_payload(score))
        confirmations.append(confirmation)
        if index in {1, count // 2, count}:
            print(f"Evidence {index:02d}/{count}: confirmed {score.get('action', 'unknown')} for {score.get('decision_id', 'unknown')}")

    final = _request(base_url, "GET", "/api/conservation/status")
    final_status = _status_label(final)
    verified = len(confirmations)
    correct = sum(
        1
        for score, confirmation in zip(scores, confirmations)
        if confirmation.get("status") not in {"paused", "held"}
        and confirmation.get("learning_applied", True) is not False
        and confirmation.get("actual_action", score.get("action")) == score.get("action")
    )
    alpha = float(final.get("alpha", 1.0 if verified else 0.0))
    q = float(final.get("q", correct / verified if verified else 0.0))
    theta = final.get("theta_min", _theta_min(alpha, verified))
    last_score = scores[-1]
    gate_status = _request(base_url, "GET", "/api/s2p/auto-approve/status")
    gate_evaluation = _request(
        base_url,
        "POST",
        "/api/s2p/auto-approve/evaluate",
        {
            "category": category,
            "confidence": float(last_score.get("confidence", 0.0)),
            "recommended_action": str(last_score.get("action", "")),
            "decision_id": str(last_score.get("decision_id", "")),
        },
    )
    auto_approve_safe = bool(gate_evaluation.get("would_auto_approve", False))

    report: dict[str, Any] = {
        "demo": "E20 conservation gate",
        "backend": base_url,
        "category": category,
        "tensor_shape": list(TENSOR_SHAPE),
        "penalty_ratio": PENALTY_RATIO,
        "theta_formula": "23.53/(alpha*V)",
        "initial": {
            "category_verified_decisions": 0,
            "category_status": "RED",
            "global_status": initial_global_status,
            "auto_approve_allowed": False,
        },
        "evidence": {
            "scores_submitted": len(scores),
            "outcomes_confirmed": len(confirmations),
            "correct_confirmations": correct,
        },
        "final": {
            "category_verified_decisions": verified,
            "category_accuracy": q,
            "global_status": final_status,
            "alpha": alpha,
            "q": q,
            "V": int(final.get("V", verified)),
            "theta_min": theta,
            "signal": final.get("signal"),
            "auto_approve": gate_evaluation,
            "gate_status": gate_status,
            "auto_approve_safe": auto_approve_safe,
        },
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"GREEN · evidence confirmed · global conservation status: {final_status}")
    if auto_approve_safe:
        print("Auto-approve now safe: True")
    else:
        print(f"Auto-approve now safe: False ({gate_evaluation.get('blocked_reason', 'gate remains closed')})")
    print(f"JSON report: {report_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://localhost:8002")
    parser.add_argument("--category", default=DEFAULT_CATEGORY)
    parser.add_argument("--count", type=int, default=INVOICE_COUNT)
    parser.add_argument("--report", type=Path, default=Path("conservation_s2p_report.json"))
    args = parser.parse_args(argv)
    try:
        run_demo(args.backend, args.category, args.count, args.report)
    except (LiveBackendError, ValueError) as exc:
        print(f"E20 failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
