"""S2P performance endpoints for trajectory, what-if, and summaries."""

from __future__ import annotations

import threading
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from gae.calibration import compute_theta_min

from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.models.responses import GenericResponse

router = APIRouter(prefix="/api/s2p/performance", tags=["s2p-performance"])

PENALTY_RATIO = 5.0
ANNUAL_TARGET_USD = 680000
SUMMARY_CACHE_TTL_SECONDS = 2.0
_SUMMARY_CACHE_LOCK = threading.RLock()
_SUMMARY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _graph_store(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is not None:
        return graph_store
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _summary_cache_key(graph_store: Any | None, domain: str | None = None) -> str:
    return f"{id(graph_store)}:{domain or 's2p'}"


def clear_summary_cache() -> None:
    with _SUMMARY_CACHE_LOCK:
        _SUMMARY_CACHE.clear()


def _graph_reader(request: Request) -> S2PGraphReader:
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    graph_store = getattr(scorer, "graph_store", None)
    state_graph_store = getattr(state, "graph_store", None)
    reader = getattr(state, "s2p_graph_reader", None)
    if isinstance(reader, S2PGraphReader) and (
        reader.store is graph_store or reader.store is state_graph_store
    ):
        return reader
    if graph_store is None:
        raise GraphUnavailableError("S2P graph reader unavailable")
    return S2PGraphReader(store=graph_store)


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


def _count_verified(reader: S2PGraphReader) -> int:
    return int(reader.count_verified())


def _count_correct(reader: S2PGraphReader) -> int:
    return int(reader.count_correct())


def _count_decisions(reader: S2PGraphReader) -> int:
    return int(reader.count_decisions())


def _count_recommended_action(reader: S2PGraphReader, action: str) -> int:
    return int(reader.count_recommended_action(action))


def _current_q(reader: S2PGraphReader) -> float:
    verified = _count_verified(reader)
    if verified <= 0:
        return 0.0
    return round(_count_correct(reader) / verified, 4)


def _verified_decisions(reader: S2PGraphReader) -> list[dict[str, Any]]:
    return reader.get_verified_decisions()


def _is_override_decision(decision: dict[str, Any]) -> bool:
    outcome = str(decision.get("outcome") or decision.get("actual_outcome") or "").strip().lower()
    if outcome in {"override", "overridden"}:
        return True
    return decision.get("is_correct") is False


def _build_summary(reader: S2PGraphReader) -> dict[str, Any]:
    total = _count_decisions(reader)
    verified = _count_verified(reader)
    correct = _count_correct(reader)
    auto_approvals = _count_recommended_action(reader, "auto_approve")
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


def _cached_summary(reader: S2PGraphReader) -> dict[str, Any]:
    now = time.monotonic()
    key = _summary_cache_key(reader.store)
    with _SUMMARY_CACHE_LOCK:
        cached = _SUMMARY_CACHE.get(key)
        if cached is not None:
            timestamp, payload = cached
            if now - timestamp <= SUMMARY_CACHE_TTL_SECONDS:
                return dict(payload)

        payload = _build_summary(reader)
        _SUMMARY_CACHE[key] = (time.monotonic(), dict(payload))
        return dict(payload)


def _projected_theta_min(
    reader: S2PGraphReader,
    new_verified: int,
    additional_incorrect: int,
) -> float:
    if new_verified <= 0:
        return 1.0
    current_overrides = sum(1 for decision in _verified_decisions(reader) if _is_override_decision(decision))
    projected_override_rate = (current_overrides + additional_incorrect) / new_verified
    if projected_override_rate <= 0:
        return 1.0
    try:
        return round(float(compute_theta_min(projected_override_rate, new_verified)), 4)
    except (TypeError, ValueError):
        return 1.0


@router.get("/trajectory", response_model=GenericResponse)
def trajectory(request: Request) -> dict[str, Any]:
    reader = _graph_reader(request)
    try:
        checkpoints = reader.store.get_centroid_checkpoints("s2p", limit=100)
        verified = _count_verified(reader)
        current_q = _current_q(reader)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for performance") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for performance") from exc
    points = _json_safe(checkpoints) if isinstance(checkpoints, list) else []
    return {
        "points": points,
        "total_checkpoints": len(points),
        "verified": verified,
        "current_q": current_q,
    }


@router.get("/what-if", response_model=GenericResponse)
def what_if(
    request: Request,
    additional_correct: int = Query(10, ge=0, le=100),
    additional_incorrect: int = Query(0, ge=0, le=100),
) -> dict[str, Any]:
    reader = _graph_reader(request)
    try:
        verified = _count_verified(reader)
        correct = _count_correct(reader)
        current_q = _current_q(reader)
        theta_min = _projected_theta_min(reader, verified + additional_correct + additional_incorrect, additional_incorrect)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for performance") from exc
    new_verified = verified + additional_correct + additional_incorrect
    new_correct = correct + additional_correct
    projected_q = round(new_correct / new_verified, 4) if new_verified else 0.0
    return {
        "current": {
            "verified": verified,
            "correct": correct,
            "q": current_q,
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


@router.get("/summary", response_model=GenericResponse)
def summary(request: Request) -> dict[str, Any]:
    try:
        return _cached_summary(_graph_reader(request))
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for performance") from exc
