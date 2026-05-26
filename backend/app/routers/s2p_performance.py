"""S2P performance endpoints for trajectory, what-if, and summaries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from gae.calibration import compute_theta_min

router = APIRouter(prefix="/api/s2p/performance", tags=["s2p-performance"])

PENALTY_RATIO = 5.0
ANNUAL_TARGET_USD = 680000


def _graph_store(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is not None:
        return graph_store
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _graph_domain(graph_store: Any | None = None) -> str:
    return str(getattr(graph_store, "domain", None) or "s2p")


def _safe_call(target: Any, name: str, default: Any, *args: Any, **kwargs: Any) -> Any:
    if target is None or not hasattr(target, name):
        return default
    try:
        return getattr(target, name)(*args, **kwargs)
    except Exception:
        return default


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return _json_safe(value.tolist())
        except Exception:
            pass
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _count_verified(graph_store: Any) -> int:
    return int(_safe_call(graph_store, "count_verified", 0, _graph_domain(graph_store)) or 0)


def _count_correct(graph_store: Any) -> int:
    return int(_safe_call(graph_store, "count_correct", 0, _graph_domain(graph_store)) or 0)


def _current_q(graph_store: Any) -> float:
    verified = _count_verified(graph_store)
    if verified <= 0:
        return 0.0
    return round(_count_correct(graph_store) / verified, 4)


def _verified_decisions(graph_store: Any) -> list[dict[str, Any]]:
    decisions = _safe_call(graph_store, "get_verified_decisions", [], _graph_domain(graph_store))
    return decisions if isinstance(decisions, list) else []


def _is_override_decision(decision: dict[str, Any]) -> bool:
    outcome = str(decision.get("outcome") or decision.get("actual_outcome") or "").strip().lower()
    if outcome in {"override", "overridden"}:
        return True
    return decision.get("is_correct") is False


def _projected_theta_min(
    graph_store: Any,
    new_verified: int,
    additional_incorrect: int,
) -> float:
    if new_verified <= 0:
        return 1.0
    current_overrides = sum(1 for decision in _verified_decisions(graph_store) if _is_override_decision(decision))
    projected_override_rate = (current_overrides + additional_incorrect) / new_verified
    if projected_override_rate <= 0:
        return 1.0
    try:
        return round(float(compute_theta_min(projected_override_rate, new_verified)), 4)
    except (TypeError, ValueError):
        return 1.0


@router.get("/trajectory")
def trajectory(request: Request) -> dict[str, Any]:
    graph_store = _graph_store(request)
    checkpoints = _safe_call(
        graph_store,
        "get_centroid_checkpoints",
        [],
        _graph_domain(graph_store),
        limit=100,
    )
    points = _json_safe(checkpoints) if isinstance(checkpoints, list) else []
    return {
        "points": points,
        "total_checkpoints": len(points),
        "verified": _count_verified(graph_store),
        "current_q": _current_q(graph_store),
    }


@router.get("/what-if")
def what_if(
    request: Request,
    additional_correct: int = Query(10, ge=0, le=100),
    additional_incorrect: int = Query(0, ge=0, le=100),
) -> dict[str, Any]:
    graph_store = _graph_store(request)
    verified = _count_verified(graph_store)
    correct = _count_correct(graph_store)
    new_verified = verified + additional_correct + additional_incorrect
    new_correct = correct + additional_correct
    projected_q = round(new_correct / new_verified, 4) if new_verified else 0.0
    theta_min = _projected_theta_min(graph_store, new_verified, additional_incorrect)
    return {
        "current": {
            "verified": verified,
            "correct": correct,
            "q": _current_q(graph_store),
        },
        "additional": {
            "correct": additional_correct,
            "incorrect": additional_incorrect,
        },
        "projected": {
            "verified": new_verified,
            "correct": new_correct,
            "q": projected_q,
            "theta_min": theta_min,
            "status": "GREEN" if projected_q >= theta_min else "RED",
        },
        "penalty_ratio": PENALTY_RATIO,
    }


@router.get("/summary")
def summary(request: Request) -> dict[str, Any]:
    graph_store = _graph_store(request)
    domain = _graph_domain(graph_store)
    decisions = _safe_call(graph_store, "get_all_decisions", [], domain)
    verified_decisions = _safe_call(graph_store, "get_verified_decisions", [], domain)
    decisions = decisions if isinstance(decisions, list) else []
    verified_decisions = verified_decisions if isinstance(verified_decisions, list) else []
    auto_approvals = sum(
        1
        for decision in decisions
        if (decision.get("recommended_action") or decision.get("action")) == "auto_approve"
    )
    total = len(decisions)
    verified = len(verified_decisions)
    correct = sum(1 for decision in verified_decisions if decision.get("is_correct") is True)
    auto_rate = round(auto_approvals / total, 4) if total else 0.0
    accuracy = round(correct / verified, 4) if verified else 0.0
    return {
        "total_scored": total,
        "total_verified": verified,
        "accuracy": accuracy,
        "auto_approve_rate": auto_rate,
        "savings_estimate_usd": round(auto_rate * total * 5000 * 0.67, 2),
        "annual_target_usd": ANNUAL_TARGET_USD,
        "penalty_ratio": PENALTY_RATIO,
    }
