"""
S2P Copilot router — domain-specific endpoints.
Framework endpoints are in framework_router.py (copied from SOC).
This file: S2P procurement domain endpoints only.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import copy
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import threading
import time
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Optional, cast

from copilot_sdk.backend.conservation_utils import compute_conservation_metrics
from copilot_sdk.scoring.mutation_lock import get_mutation_lock, serialize_mutation
from copilot_sdk.scoring.dk_persistence import (
    DKWelfordTracker,
    persist_dk_after_reestimate,
)
from copilot_sdk.state.invalidation import apply_cache_invalidation_event
from copilot_sdk.state.cached_static import cached_static

from app.domains.s2p.auto_approve import (
    AUTO_APPROVE_THRESHOLDS,
    build_expansion_proof,
    get_auto_approve_stats,
    record_auto_approve_decision,
    _should_auto_approve,
)
from app.domains.s2p.config import PENALTY_RATIO, S2PDomainConfig
from app.domains.s2p.factors import S2PEvent, compute_all_factors
from app.models.responses import GenericResponse, LearningGateResponse, S2PScoreResponse
from app.routers.s2p_data_helpers import find_invoice, load_invoices
from app.routers.s2p_preview import _load_celonis_cache
from app.models.outcome_receipt import OutcomeReceipt
from app.services.receipt_store import get_receipt_store
from app.services.cross_copilot_signals import (
    CrossCopilotSignalConsumer,
    latest_supplier_signal,
    supplier_exception_from_reliability,
)
from app.services.novelty_tracker import compute_nearest_distance, get_novelty_tracker
from app.services.s2p_evolver import get_active_variant, record_triage_outcome
from app.services.supplier_profile_accumulator import accumulator as supplier_profile_accumulator
from app.s2p_shadow import S2PShadowState

router = APIRouter(prefix="/api/s2p", tags=["S2P"])
learn_router = APIRouter(prefix="/api", tags=["S2P"])
log = logging.getLogger(__name__)
_SIDE_EFFECT_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="s2p-side-effect")
_GRAPH_LINK_ADVISORY_LOCK = threading.RLock()
_SCORE_CONSERVATION_STATUS_TTL_SECONDS = 2.0
_SCORE_CONSERVATION_STATUS_LOCK = threading.RLock()
_SCORE_PROCESS_CONTEXT_LOCK = threading.RLock()
_L5_CONSERVATION_STATE_LOCK = threading.RLock()
_L5_DK_STATE_LOCK = threading.RLock()
_L5_CENTROID_STATE_LOCK = threading.RLock()
_S2P_DK_WELFORD_TRACKER = DKWelfordTracker()


def set_l5_dk_welford_tracker(tracker: DKWelfordTracker | None) -> None:
    """Replace the process-local S2P DK Welford tracker after startup restore."""
    global _S2P_DK_WELFORD_TRACKER
    if tracker is None:
        return
    with _L5_DK_STATE_LOCK:
        _S2P_DK_WELFORD_TRACKER = tracker
_SCORE_CONSERVATION_STATUS_CACHE: dict[str, tuple[float, str]] = {}
_CONSERVATION_COUNTS_CACHE: dict[str, tuple[float, dict[str, float | int]]] = {}
_SCORE_PROCESS_CONTEXT_CACHE: tuple[int, dict] | None = None


def _log_side_effect_failure(future: Future) -> None:
    try:
        exc = future.exception()
    except Exception as callback_exc:
        log.warning("S2P side effect status check failed: %s", callback_exc, exc_info=True)
        return
    if exc is not None:
        log.warning(
            "S2P side effect failed: %s",
            exc,
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def _submit_side_effect(fn):
    """Submit a non-essential side effect to the bounded executor."""
    future = _SIDE_EFFECT_EXECUTOR.submit(fn)
    future.add_done_callback(_log_side_effect_failure)
    return future

def _resolve_graph_context(invoice_id: str, http_request: Request):
    state = getattr(http_request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is None:
        scorer = getattr(state, "scorer", None)
        graph_store = getattr(scorer, "graph_store", None)
    if graph_store is None or not hasattr(graph_store, "query_context"):
        return None
    try:
        context_raw = graph_store.query_context(invoice_id, 2)
    except Exception:
        return None
    if isinstance(context_raw, list):
        if not any(
            isinstance(row, dict) and _graph_context_row_is_domain_specific(row)
            for row in context_raw
        ):
            return None
        return {"neighbors": context_raw}
    if isinstance(context_raw, dict):
        return context_raw
    return None


def _graph_context_row_is_domain_specific(row: dict[str, Any]) -> bool:
    node = row.get("node")
    if isinstance(node, str):
        return node not in {"entity", "decision"}
    if isinstance(node, dict):
        return True
    return bool(node)


def _compute_score_process_context() -> dict | None:
    celonis_data = _load_celonis_cache()
    activities = celonis_data.get("activities")
    if not isinstance(activities, list):
        return None

    bottleneck = next(
        (
            activity
            for activity in activities
            if isinstance(activity, dict) and activity.get("bottleneck") is True
        ),
        None,
    )
    if not bottleneck:
        return None

    duration_hours = float(
        bottleneck.get("duration_median_hours", bottleneck.get("avg_duration_hours", 0.0)) or 0.0
    )
    process_context = {
        "bottleneck_activity": bottleneck.get("name") or bottleneck.get("id"),
        "duration_median_min": round(duration_hours * 60.0, 2),
        "source": "celonis_cache",
    }
    cause = bottleneck.get("bottleneck_cause") or bottleneck.get("cause") or bottleneck.get("root_cause")
    if cause:
        process_context["cause"] = cause
    return process_context


def _score_process_context() -> dict | None:
    global _SCORE_PROCESS_CONTEXT_CACHE
    with _SCORE_PROCESS_CONTEXT_LOCK:
        loader_id = id(_load_celonis_cache)
        if _SCORE_PROCESS_CONTEXT_CACHE is None or _SCORE_PROCESS_CONTEXT_CACHE[0] != loader_id:
            _SCORE_PROCESS_CONTEXT_CACHE = (loader_id, _compute_score_process_context() or {})
        process_context = _SCORE_PROCESS_CONTEXT_CACHE[1]
        return dict(process_context) if process_context else None


def _score_process_context_with_signal(signal: dict[str, Any] | None) -> dict | None:
    process_context = _score_process_context()
    if signal is None:
        return process_context
    output = dict(process_context or {})
    output["cross_copilot_signal"] = signal
    return output


def _supplier_name_for_signal(invoice: dict[str, Any], request: "ScoreRequest") -> str:
    return str(
        invoice.get("supplier_name")
        or invoice.get("supplier")
        or invoice.get("vendor_name")
        or getattr(request, "supplier_name", None)
        or invoice.get("supplier_id")
        or request.supplier_id
        or ""
    )


def _apply_cross_copilot_signal(invoice: dict[str, Any], request: "ScoreRequest") -> dict[str, Any] | None:
    supplier_name = _supplier_name_for_signal(invoice, request)
    latest = latest_supplier_signal(CrossCopilotSignalConsumer().fetch_supplier_signals(supplier_name))
    if latest is None:
        return None

    signal_exception = supplier_exception_from_reliability(latest.get("reliability_pct"))
    factors = dict(invoice.get("factors") or {})
    current_exception = _to_float(
        invoice.get("supplier_exception_history", factors.get("supplier_exception_history")),
        0.0,
    )
    tightened_exception = max(current_exception, signal_exception)
    factors["supplier_exception_history"] = tightened_exception
    invoice["factors"] = factors
    invoice["supplier_exception_history"] = tightened_exception
    invoice["supplier_risk_rating"] = max(0.0, min(_to_float(latest.get("reliability_pct"), 100.0), 100.0)) / 100.0

    raw_delta = latest.get("delta")
    delta = _to_float(raw_delta, 0.0) if raw_delta is not None else None
    reliability = _to_float(latest.get("reliability_pct"), 0.0)
    warning = (
        f"Purchasing: reliability dropped {abs(delta):.0f}pp"
        if delta is not None
        else f"Purchasing: reliability {reliability:.0f}%"
    )
    return {
        "source": "purchasing",
        "supplier": str(latest.get("supplier_name") or supplier_name),
        "reliability": reliability,
        "delta": delta,
        "warning": warning,
        "supplier_exception_history": tightened_exception,
        "supplier_risk_rating": invoice["supplier_risk_rating"],
        "timestamp": latest.get("timestamp"),
        "ttl_days": latest.get("ttl_days", 7),
        "provenance": "signal",
    }


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sdk_scorer(http_request: Request):
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        raise HTTPException(status_code=500, detail="S2P scorer is not configured")
    return scorer


def _s2p_shadow_state(http_request: Request) -> S2PShadowState | None:
    state = getattr(http_request.app, "state", None)
    shadow = getattr(state, "s2p_shadow", None)
    return shadow if isinstance(shadow, S2PShadowState) else None


def _shadow_latency_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)


def _handle_shadow_error(
    shadow: S2PShadowState,
    *,
    operation: str,
    operation_id: str,
    error: BaseException,
    start: float,
) -> None:
    shadow.diagnostics.record(
        operation=operation,
        status="failed",
        operation_id=operation_id,
        error=error,
        latency_ms=_shadow_latency_ms(start),
    )
    if shadow.config.strict:
        raise HTTPException(
            status_code=502,
            detail=f"S2P AGE shadow {operation} failed: {error.__class__.__name__}",
        ) from error


def _record_score_shadow(
    http_request: Request,
    *,
    score_request: "ScoreRequest",
    score_result: Any,
    factor_vector: list[float],
    invoice: dict[str, Any],
    active_variant: dict[str, Any] | None,
) -> None:
    shadow = _s2p_shadow_state(http_request)
    if shadow is None or not shadow.config.enabled:
        return
    if shadow.store is None:
        shadow.diagnostics.record(
            operation="score_shadow",
            status="skipped",
            operation_id=str(getattr(score_result, "decision_id", "")),
            parity={"reason": "shadow_store_unavailable"},
        )
        return

    operation_id = str(score_result.decision_id)
    start = time.perf_counter()
    try:
        shadow.store.write_governed_decision(
            decision_id=score_result.decision_id,
            domain=shadow.config.domain,
            category=score_request.category,
            category_index=S2PDomainConfig.get_category_index(score_request.category),
            recommended_action=score_result.action,
            recommended_index=score_result.action_index,
            confidence=score_result.confidence,
            probabilities=score_result.probabilities,
            factor_vector=factor_vector,
            factor_names=list(S2PDomainConfig.factors),
            source="s2p_score_shadow",
            scorer_version="s2p_shadow_phase2",
            preset_version="s2p",
            factor_schema_version="s2p_factor_schema_v1",
            metadata={
                "shadow": True,
                "shadow_run_id": shadow.diagnostics.shadow_run_id,
                "shadow_operation": "score_shadow",
                "operation_id": operation_id,
                "event_id": score_request.event_id,
                "invoice_id": invoice.get("invoice_id") or score_request.event_id,
                "supplier_id": score_request.supplier_id,
                "amount": score_request.amount,
                "active_variant": active_variant,
            },
        )
    except Exception as exc:
        _handle_shadow_error(
            shadow,
            operation="score_shadow",
            operation_id=operation_id,
            error=exc,
            start=start,
        )
        return

    shadow.diagnostics.record(
        operation="score_shadow",
        status="succeeded",
        operation_id=operation_id,
        latency_ms=_shadow_latency_ms(start),
        parity={
            "decision_id": score_result.decision_id,
            "decision_id_match": True,
            "status_match": True,
        },
    )


def _record_outcome_shadow(
    http_request: Request,
    *,
    operation: str,
    decision: dict[str, Any] | None,
    decision_id: str,
    actual_action: str,
    outcome: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    shadow = _s2p_shadow_state(http_request)
    if shadow is None or not shadow.config.enabled:
        return
    if shadow.store is None:
        shadow.diagnostics.record(
            operation=operation,
            status="skipped",
            operation_id=decision_id,
            parity={"reason": "shadow_store_unavailable"},
        )
        return

    recommended_action = _decision_recommended_action(decision)
    is_correct = str(actual_action) == str(recommended_action) if recommended_action else outcome in {
        "confirm",
        "confirmed",
    }
    start = time.perf_counter()
    try:
        shadow.store.write_outcome(
            decision_id=decision_id,
            actual_action=actual_action,
            is_correct=is_correct,
            domain=shadow.config.domain,
            metadata={
                "shadow": True,
                "shadow_run_id": shadow.diagnostics.shadow_run_id,
                "shadow_operation": operation,
                "operation_id": decision_id,
                "outcome": outcome,
                **dict(metadata or {}),
            },
        )
    except Exception as exc:
        _handle_shadow_error(
            shadow,
            operation=operation,
            operation_id=decision_id,
            error=exc,
            start=start,
        )
        return

    shadow.diagnostics.record(
        operation=operation,
        status="succeeded",
        operation_id=decision_id,
        latency_ms=_shadow_latency_ms(start),
        parity={
            "decision_id": decision_id,
            "decision_id_match": True,
            "outcome_match": True,
            "is_correct": is_correct,
        },
    )


def _record_score_novelty(
    factor_vector: list[float],
    category: str,
    scorer: Any,
) -> float | None:
    try:
        nearest_distance = compute_nearest_distance(
            factor_vector,
            category,
            scorer,
            S2PDomainConfig,
        )
        novelty_score = _json_safe_float(nearest_distance)
        record_distance = novelty_score if novelty_score is not None else 0.0
        tracker = get_novelty_tracker()
        tracker.record(factor_vector, category, record_distance)
        return novelty_score
    except (AttributeError, TypeError, ValueError, IndexError) as exc:
        log.warning("S2P novelty observation skipped: %s", exc)
        return None


def _snapshot_score_centroids(scorer: Any) -> Any:
    gae_scorer = getattr(scorer, "gae_scorer", None)
    centroids = getattr(gae_scorer, "centroids", None) if gae_scorer is not None else None
    if centroids is None:
        centroids = getattr(scorer, "centroids", None)
    if centroids is None:
        return None
    if hasattr(centroids, "copy"):
        return centroids.copy()
    return copy.deepcopy(centroids)


def _record_score_novelty_from_snapshot(
    factor_vector: list[float],
    category: str,
    centroid_snapshot: Any,
    scorer: Any,
) -> float | None:
    if centroid_snapshot is None:
        return None
    snapshot_scorer = type("CentroidSnapshotScorer", (), {"centroids": centroid_snapshot})()
    return _record_score_novelty(factor_vector, category, snapshot_scorer)


def _json_safe_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _graph_store_from_request(http_request: Request):
    state = getattr(http_request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is None:
        scorer = getattr(state, "scorer", None)
        graph_store = getattr(scorer, "graph_store", None)
    return graph_store


def _graph_domain(graph_store: Any | None = None) -> str:
    return str(getattr(graph_store, "domain", None) or "s2p")


def _learning_store_from_request(http_request: Request) -> Any | None:
    state = getattr(http_request.app, "state", None)
    for candidate in (
        getattr(state, "learning_store", None),
        getattr(state, "_learning_store", None),
        _graph_store_from_request(http_request),
    ):
        if candidate is None:
            continue
        if callable(getattr(candidate, "get_conservation_state", None)) and callable(
            getattr(candidate, "update_conservation_state", None)
        ):
            return candidate
    return None


def _dk_learning_store_from_request(http_request: Request) -> Any | None:
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    for candidate in (
        getattr(state, "learning_store", None),
        getattr(state, "_learning_store", None),
        getattr(scorer, "learning_store", None),
        getattr(scorer, "_learning_store", None),
        _graph_store_from_request(http_request),
    ):
        if candidate is None:
            continue
        if callable(getattr(candidate, "update_dk_weights", None)):
            return candidate
    return None


def _centroid_learning_store_from_request(http_request: Request) -> Any | None:
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    for candidate in (
        getattr(state, "learning_store", None),
        getattr(state, "_learning_store", None),
        getattr(scorer, "learning_store", None),
        getattr(scorer, "_learning_store", None),
        _graph_store_from_request(http_request),
    ):
        if candidate is None:
            continue
        if callable(getattr(candidate, "update_centroid", None)):
            return candidate
    return None


def _persist_l5_conservation_state(http_request: Request, decision_id: str | None) -> None:
    store = _learning_store_from_request(http_request)
    if store is None:
        return None
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        return None
    domain = _graph_domain(_graph_store_from_request(http_request))
    try:
        metrics = compute_conservation_metrics(scorer, domain=domain)
    except Exception as exc:
        log.warning("S2P L5 conservation state skipped: %s", exc)
        return None
    with _L5_CONSERVATION_STATE_LOCK:
        try:
            old_state = store.get_conservation_state(domain)
        except Exception as exc:
            log.warning("S2P L5 conservation state read failed: %s", exc)
            return None
        old_status = None
        if isinstance(old_state, dict):
            stored_status = old_state.get("status")
            old_status = None if stored_status is None else str(stored_status)
        try:
            store.update_conservation_state(
                domain=domain,
                status=str(metrics["status"]),
                alpha=float(metrics["alpha"]),
                q=float(metrics["q"]),
                V=int(metrics["V"]),
                theta_min=float(metrics["theta_min"]),
                product=float(metrics["product"]),
                categories_total=int(metrics["categories_total"]),
                categories_with_data=int(metrics["categories_with_data"]),
                baseline_product=float(metrics["baseline_product"]),
                relative_threshold=float(metrics["relative_threshold"]),
                complacency_flag=str(metrics["complacency_flag"]),
                caused_by_decision_id=decision_id,
                old_status=old_status,
            )
        except Exception as exc:
            log.warning("S2P L5 conservation state write failed: %s", exc)
    return None


def _persist_l5_centroid_state(
    http_request: Request,
    *,
    scorer: Any,
    category: str | None,
    actual_action: str,
    decision_id: str,
    pre_centroid: list[float] | None,
) -> bool:
    store = _centroid_learning_store_from_request(http_request)
    if store is None or not category:
        return False
    get_phase = getattr(scorer, "get_category_phase", None)
    get_centroid = getattr(scorer, "get_centroid", None)
    if not callable(get_phase) or not callable(get_centroid):
        return False
    try:
        phase = str(get_phase(category))
    except Exception as exc:
        log.debug("S2P L5 centroid persistence skipped: phase unavailable: %s", exc)
        return False
    if phase == "VARIANCE_LEARNING":
        return False
    if phase != "MEAN_CONVERGENCE":
        log.debug("S2P L5 centroid persistence skipped: unknown phase %s", phase)
        return False
    try:
        post_centroid = get_centroid(category, actual_action)
    except Exception as exc:
        log.debug("S2P L5 centroid persistence skipped: centroid unavailable: %s", exc)
        return False
    if post_centroid is None:
        return False
    try:
        post_vector = [float(item) for item in post_centroid]
    except (TypeError, ValueError):
        return False
    if not post_vector or not all(math.isfinite(item) for item in post_vector):
        return False
    delta_norm = _centroid_delta_norm(pre_centroid, post_vector)
    domain = _graph_domain(_graph_store_from_request(http_request))
    try:
        with _L5_CENTROID_STATE_LOCK:
            store.update_centroid(
                domain=domain,
                category=str(category),
                action=str(actual_action),
                centroid_vector=post_vector,
                delta_norm=delta_norm,
                caused_by_decision_id=decision_id,
            )
    except Exception as exc:
        log.warning("S2P L5 centroid write failed: %s", exc)
        return False
    return True


def _persist_l5_dk_state(
    http_request: Request,
    *,
    decision: dict[str, Any] | None,
    actual_action: str,
    payload: dict[str, Any],
) -> None:
    if payload.get("status") == "paused" or payload.get("learning_applied") is False:
        return None
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        return None
    factor_vector = _decision_factor_vector_for_dk(decision)
    recommended_action = _decision_recommended_action(decision)
    if factor_vector is None or recommended_action is None:
        log.warning("S2P L5 DK persistence skipped: missing decision factor/action data")
        return None
    reestimate = getattr(scorer, "reestimate_dk_if_due", None)
    get_dk_weights = getattr(scorer, "get_dk_weights", None)
    if not callable(reestimate) or not callable(get_dk_weights):
        log.warning("S2P L5 DK persistence skipped: scorer lacks DK runtime helpers")
        return None
    domain = _graph_domain(_graph_store_from_request(http_request))
    is_correct = str(actual_action) == str(recommended_action)
    try:
        with _L5_DK_STATE_LOCK:
            _S2P_DK_WELFORD_TRACKER.update(factor_vector, is_correct)
            reestimate()
            store = _dk_learning_store_from_request(http_request)
            if store is None:
                return None
            if get_dk_weights() is None:
                return None
            persist_dk_after_reestimate(
                domain=domain,
                scorer=scorer,
                learning_store=store,
                welford_tracker=_S2P_DK_WELFORD_TRACKER,
                entity_group=None,
                logger=log,
            )
    except Exception as exc:
        log.warning("S2P L5 DK persistence skipped: %s", exc)
    return None


def _decision_factor_vector_for_dk(decision: dict[str, Any] | None) -> list[float] | None:
    if not isinstance(decision, dict):
        return None
    value = _decision_lookup(decision, "factor_vector")
    if value is None:
        value = _decision_lookup(decision, "factors")
    if isinstance(value, dict):
        return [float(value[name]) for name in value]
    if isinstance(value, (str, bytes, bytearray)) or value is None:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _read_centroid_for_l5(
    scorer: Any,
    *,
    category: str | None,
    action: str,
) -> list[float] | None:
    if not category:
        return None
    get_centroid = getattr(scorer, "get_centroid", None)
    if not callable(get_centroid):
        return None
    try:
        centroid = get_centroid(category, action)
    except Exception as exc:
        log.debug("S2P L5 centroid pre-read skipped: %s", exc)
        return None
    if centroid is None:
        return None
    try:
        return [float(item) for item in centroid]
    except (TypeError, ValueError):
        return None


def _centroid_delta_norm(
    pre_centroid: list[float] | None,
    post_centroid: list[float],
) -> float:
    if pre_centroid is None or len(pre_centroid) != len(post_centroid):
        return float(math.sqrt(sum(value * value for value in post_centroid)))
    return float(
        math.sqrt(
            sum(
                (post - pre) * (post - pre)
                for pre, post in zip(pre_centroid, post_centroid, strict=False)
            )
        )
    )


def _decision_lookup(decision: dict[str, Any], key: str) -> Any:
    if key in decision:
        return decision[key]
    metadata = decision.get("metadata")
    if isinstance(metadata, dict) and key in metadata:
        return metadata[key]
    return None


def _link_decision_to_invoice(
    graph_store: Any,
    decision_id: str | None,
    invoice_id: str | None,
) -> None:
    if not decision_id or not invoice_id:
        return
    link = getattr(graph_store, "link_decision_to_entity", None)
    if not callable(link):
        return
    try:
        get_links = getattr(graph_store, "get_decision_links", None)
        if callable(get_links):
            existing = get_links(decision_id)
            if any(
                item.get("entity_id") == invoice_id
                and item.get("edge_type") == "DECIDED_ON"
                for item in existing
                if isinstance(item, dict)
            ):
                return
        link(decision_id, invoice_id, "DECIDED_ON")
    except Exception:
        log.exception("S2P graph invoice link skipped for decision %s", decision_id)


def _has_decision_invoice_link(graph_store: Any, decision_id: str | None, invoice_id: str | None) -> bool:
    if not decision_id or not invoice_id:
        return False
    get_links = getattr(graph_store, "get_decision_links", None)
    if not callable(get_links):
        return False
    try:
        return any(
            str(item.get("entity_id")) == str(invoice_id)
            and item.get("edge_type") == "DECIDED_ON"
            for item in get_links(decision_id)
            if isinstance(item, dict)
        )
    except Exception:
        return False


def _graph_verified_counts(http_request: Request) -> tuple[int, int]:
    graph_store = _graph_store_from_request(http_request)
    domain = _graph_domain(graph_store)
    counts = _cached_conservation_counts(graph_store, domain)
    return int(counts["verified_count"]), int(counts["correct_count"])


def _conservation_cache_key(graph_store: Any | None, domain: str | None = None) -> str:
    selected_domain = domain or _graph_domain(graph_store)
    return f"{id(graph_store)}:{selected_domain}"


def _read_conservation_counts(
    graph_store: Any | None,
    domain: str | None = None,
) -> dict[str, float | int]:
    selected_domain = domain or _graph_domain(graph_store)
    count_verified = getattr(graph_store, "count_verified", None)
    count_correct = getattr(graph_store, "count_correct", None)
    verified_count = int(count_verified(selected_domain)) if callable(count_verified) else 0
    correct_count = int(count_correct(selected_domain)) if callable(count_correct) else 0
    # Conservation V is verified decisions only; pending decisions are excluded.
    count_verified_decisions = getattr(graph_store, "count_verified_decisions", None)
    if callable(count_verified_decisions):
        total_decisions = int(count_verified_decisions(selected_domain))
    else:
        total_decisions = verified_count
    return {
        "verified_count": max(verified_count, 0),
        "correct_count": max(correct_count, 0),
        "total_decisions": max(int(total_decisions), 0),
        "penalty_ratio": PENALTY_RATIO,
    }


def _cached_conservation_counts(
    graph_store: Any | None,
    domain: str | None = None,
) -> dict[str, float | int]:
    now = time.monotonic()
    key = _conservation_cache_key(graph_store, domain)
    with _SCORE_CONSERVATION_STATUS_LOCK:
        cached = _CONSERVATION_COUNTS_CACHE.get(key)
        if cached is not None:
            timestamp, counts = cached
            if now - timestamp <= _SCORE_CONSERVATION_STATUS_TTL_SECONDS:
                return dict(counts)

        counts = _read_conservation_counts(graph_store, domain)
        _CONSERVATION_COUNTS_CACHE[key] = (time.monotonic(), dict(counts))
        return dict(counts)


def cached_conservation_state_provider(app_state: Any) -> dict[str, float | int]:
    graph_store = getattr(app_state, "graph_store", None)
    if graph_store is None:
        scorer = getattr(app_state, "scorer", None)
        graph_store = getattr(scorer, "graph_store", None)
    return _cached_conservation_counts(graph_store, _graph_domain(graph_store))


def _current_conservation_status(http_request: Request) -> str:
    try:
        from gae.calibration import conservation_status

        graph_store = _graph_store_from_request(http_request)
        counts = _cached_conservation_counts(graph_store, _graph_domain(graph_store))
        check = conservation_status(
            verified_count=int(counts["verified_count"]),
            correct_count=int(counts["correct_count"]),
            total_decisions=int(counts["total_decisions"]),
            penalty_ratio=float(counts["penalty_ratio"]),
        )
        return str(check.status)
    except Exception:
        log.exception("Unable to evaluate conservation status for auto-approve gate")
        return "UNKNOWN"


def _score_conservation_cache_key(http_request: Request) -> str:
    graph_store = _graph_store_from_request(http_request)
    return f"{id(graph_store)}:{_graph_domain(graph_store)}"


def _clear_score_conservation_status_cache() -> None:
    with _SCORE_CONSERVATION_STATUS_LOCK:
        _SCORE_CONSERVATION_STATUS_CACHE.clear()
        _CONSERVATION_COUNTS_CACHE.clear()


def _score_conservation_status(http_request: Request) -> str:
    """Short-lived score-path cache for expensive graph-wide conservation checks."""
    now = time.monotonic()
    key = _score_conservation_cache_key(http_request)
    with _SCORE_CONSERVATION_STATUS_LOCK:
        cached = _SCORE_CONSERVATION_STATUS_CACHE.get(key)
        if cached is not None:
            timestamp, status = cached
            if now - timestamp <= _SCORE_CONSERVATION_STATUS_TTL_SECONDS:
                return status

        status = _current_conservation_status(http_request)
        _SCORE_CONSERVATION_STATUS_CACHE[key] = (time.monotonic(), status)
        return status


def _cached_score_conservation_status_only(http_request: Request) -> str:
    """Read score-path conservation status cache without graph/scorer I/O."""
    now = time.monotonic()
    key = _score_conservation_cache_key(http_request)
    with _SCORE_CONSERVATION_STATUS_LOCK:
        cached = _SCORE_CONSERVATION_STATUS_CACHE.get(key)
        if cached is None:
            return "UNKNOWN"
        timestamp, status = cached
        if now - timestamp > _SCORE_CONSERVATION_STATUS_TTL_SECONDS:
            return "UNKNOWN"
        return status


def _is_learning_paused(conservation: Any) -> bool:
    if conservation is None:
        return False
    if isinstance(conservation, str):
        status = conservation.strip().upper()
        return status in {"RED", "AMBER", "BOOTSTRAP", "PAUSED", "UNKNOWN"}
    if isinstance(conservation, dict):
        if conservation.get("learning_paused") is True:
            return True
        for key in ("phase", "status", "state"):
            value = conservation.get(key)
            if value is None:
                continue
            status = str(value).strip().upper()
            if status in {"RED", "AMBER", "BOOTSTRAP", "PAUSED", "UNKNOWN"}:
                return True
    return False


def _record_evolver_outcome_if_allowed(
    payload: dict[str, Any],
    variant_id: str | None,
    *,
    reward: float | None,
    category: str,
    http_request: Request,
) -> None:
    if not variant_id:
        payload["evolution_recorded"] = False
        payload["evolution_note"] = "variant_id not provided"
        return

    if _is_learning_paused(_current_conservation_status(http_request)):
        payload["evolution_recorded"] = False
        payload["evolution_note"] = "learning paused by conservation"
        return

    if reward is None:
        payload["evolution_recorded"] = False
        payload["evolution_note"] = "evolver recording skipped: reward not available"
        log.warning("S2P evolver recording skipped for variant %s: reward not available", variant_id)
        return

    try:
        record_triage_outcome(
            variant_id,
            reward=reward,
            category=category,
        )
    except ValueError as exc:
        payload["evolution_recorded"] = False
        payload["evolution_note"] = f"evolver recording skipped: {exc}"
        log.warning("S2P evolver recording failed for variant %s: %s", variant_id, exc, exc_info=True)
        return
    payload["active_variant_id"] = variant_id
    payload["evolution_recorded"] = True


def _invoice_decision_metadata(invoice: dict[str, Any]) -> dict[str, Any]:
    invoice_id = str(invoice.get("invoice_id") or invoice.get("event_id") or "")
    raw_invoice_metadata = invoice.get("metadata")
    invoice_metadata: dict[str, Any] = (
        cast(dict[str, Any], raw_invoice_metadata)
        if isinstance(raw_invoice_metadata, dict)
        else {}
    )
    metadata = {
        "invoice_id": invoice_id,
        "source_invoice_id": invoice_id,
        "supplier_id": invoice.get("supplier_id"),
        "supplier_name": invoice.get("supplier_name"),
        "amount": invoice.get("amount"),
        "invoice_date": invoice.get("invoice_date") or invoice_metadata.get("invoice_date"),
        "due_date": invoice.get("due_date") or invoice_metadata.get("due_date"),
        "po_number": invoice.get("po_number") or invoice_metadata.get("po_number"),
    }
    temporal_keys = {"invoice_date", "due_date", "po_number"}
    return {
        key: value
        for key, value in metadata.items()
        if value != "" and (value is not None or key in temporal_keys)
    }


def _decision_metadata(decision: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(decision, dict):
        return {}
    metadata = decision.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _decision_invoice_id(decision: dict[str, Any] | None) -> str:
    if not isinstance(decision, dict):
        return ""
    metadata = _decision_metadata(decision)
    for key in ("invoice_id", "source_invoice_id", "entity_id"):
        value = metadata.get(key) or decision.get(key)
        if value:
            return str(value)
    return ""


def _decision_category(decision: dict[str, Any] | None) -> str | None:
    if not isinstance(decision, dict):
        return None
    value = decision.get("category")
    if value:
        return str(value)
    metadata = _decision_metadata(decision)
    value = metadata.get("category")
    return str(value) if value else None


def _decision_recommended_action(decision: dict[str, Any] | None) -> str:
    if not isinstance(decision, dict):
        return ""
    value = decision.get("recommended_action") or decision.get("action")
    if value:
        return str(value)
    metadata = _decision_metadata(decision)
    value = metadata.get("recommended_action") or metadata.get("action")
    return str(value) if value else ""


def _decision_confidence(decision: dict[str, Any] | None) -> float:
    if not isinstance(decision, dict):
        return 0.0
    value = decision.get("confidence")
    if value is None:
        value = _decision_metadata(decision).get("confidence")
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _decision_factor_vector(decision: dict[str, Any] | None) -> list[float]:
    if not isinstance(decision, dict):
        return []
    metadata = _decision_metadata(decision)
    raw_vector = decision.get("factor_vector") or metadata.get("factor_vector")
    if isinstance(raw_vector, list):
        try:
            return [float(value) for value in raw_vector]
        except (TypeError, ValueError):
            return []
    factors = decision.get("factors")
    if isinstance(factors, dict):
        try:
            return [float(factors.get(name, 0.0) or 0.0) for name in S2PDomainConfig.factors]
        except (TypeError, ValueError):
            return []
    return []


def _decision_factors(decision: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(decision, dict):
        return {}
    factors = decision.get("factors")
    if not isinstance(factors, dict):
        factors = _decision_metadata(decision).get("factors")
    if isinstance(factors, dict):
        try:
            return {name: float(factors.get(name, 0.0) or 0.0) for name in S2PDomainConfig.factors}
        except (TypeError, ValueError):
            return {}
    vector = _decision_factor_vector(decision)
    if vector:
        return {
            name: float(vector[index])
            for index, name in enumerate(S2PDomainConfig.factors)
            if index < len(vector)
        }
    return {}


def _receipt_conservation_snapshot(http_request: Request) -> dict[str, Any]:
    try:
        status = _current_conservation_status(http_request)
        verified_count, _correct_count = _graph_verified_counts(http_request)
        return {"state": status, "verified_count": verified_count}
    except Exception:
        log.exception("Unable to capture S2P receipt conservation snapshot")
        return {"state": "", "verified_count": 0}


def _receipt_centroid_updated(payload: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> bool:
    if payload.get("status") == "paused" or payload.get("learning_applied") is False:
        return False
    return int(after.get("verified_count", 0)) > int(before.get("verified_count", 0))


def _outcome_recorded_for_receipt(
    learn_result: dict[str, Any],
    verified_before: int | None,
    verified_after: int | None,
) -> bool:
    if not isinstance(learn_result, dict):
        return False

    status = str(learn_result.get("status") or "").strip().lower()
    reason = str(learn_result.get("reason") or "").strip().lower()
    if status == "paused" or reason in {"conservation_red", "conservation_pause", "learning_paused"}:
        return False

    for key in ("outcome_recorded", "recorded", "learning_applied"):
        if learn_result.get(key) is True:
            return True

    if status in {"recorded", "learned", "confirmed", "ok", "success"}:
        return True

    if verified_before is not None and verified_after is not None:
        return int(verified_after) > int(verified_before)

    return False


def _receipt_amount_recovered(is_correct: bool | None, amount: Any) -> float | None:
    if is_correct is False:
        return 0.0
    if is_correct is True:
        try:
            return float(amount)
        except (TypeError, ValueError):
            # Correct outcomes without invoice amount metadata cannot recover a financial amount.
            return 0.0
    return None


def _receipt_invoice_number(context: dict[str, Any], decision_id: str) -> str | None:
    for key in ("invoice_number", "source_invoice_id", "invoice_id"):
        value = context.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != decision_id:
            return text
    return None


def _record_outcome_receipt(
    *,
    decision: dict[str, Any] | None,
    payload: dict[str, Any],
    actual_action: str,
    reason_code: str | None,
    conservation_before: dict[str, Any],
    conservation_after: dict[str, Any],
    evidence_receipt: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    try:
        store = get_receipt_store()
        context = _decision_context(decision, context)
        invoice_id = str(payload.get("invoice_id") or _decision_invoice_id(decision) or "")
        decision_id = str(payload.get("decision_id") or (decision or {}).get("decision_id") or "")
        recommended_action = _decision_recommended_action(decision)
        factors = _decision_factors(decision)
        is_correct = actual_action == recommended_action if recommended_action else None
        amount = context.get("amount") or context.get("total_amount")
        receipt = OutcomeReceipt(
            receipt_id=f"RCPT-{uuid4()}",
            invoice_id=invoice_id,
            decision_id=decision_id or None,
            timestamp=datetime.now(timezone.utc).isoformat(),
            scored_action=recommended_action,
            recommended_action=recommended_action,
            confidence=_decision_confidence(decision),
            factor_vector=_decision_factor_vector(decision),
            factors=factors or None,
            category=_decision_category(decision) or "",
            human_action=actual_action,
            actual_action=actual_action,
            is_correct=is_correct,
            override_reason=reason_code,
            amount=amount,
            amount_at_risk=context.get("at_risk") or context.get("amount_at_risk"),
            reward=float(payload.get("reward") or 0.0),
            centroid_updated=_receipt_centroid_updated(payload, conservation_before, conservation_after),
            conservation_status=str(conservation_after.get("state") or conservation_before.get("state") or "UNKNOWN"),
            conservation_state_before=str(conservation_before.get("state") or ""),
            conservation_state_after=str(conservation_after.get("state") or ""),
            verified_count_before=int(conservation_before.get("verified_count") or 0),
            verified_count_after=int(conservation_after.get("verified_count") or 0),
            previous_receipt_hash=store.last_hash,
            amount_recovered=_receipt_amount_recovered(is_correct, amount),
            supplier_name=context.get("supplier_name"),
            invoice_number=_receipt_invoice_number(context, decision_id),
            po_number=context.get("po_number"),
            cycle_time_saved=context.get("cycle_time_saved"),
            weight_updated=_receipt_centroid_updated(payload, conservation_before, conservation_after),
            exportable=bool(
                isinstance(evidence_receipt, dict)
                and evidence_receipt.get("payload_hash")
                and evidence_receipt.get("receipt_queued") is False
            ),
        )
        store.add(receipt)
    except (TypeError, ValueError, AttributeError) as exc:
        log.warning("S2P outcome receipt creation skipped: %s", exc)


def _receipt_factor_hash(decision: dict[str, Any] | None) -> str:
    factors = _decision_factors(decision)
    if factors:
        payload: Any = {name: factors.get(name, 0.0) for name in S2PDomainConfig.factors}
    else:
        payload = _decision_factor_vector(decision)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evidence_receipt_payload(
    *,
    decision: dict[str, Any] | None,
    decision_id: str,
    actual_action: str,
    outcome: str,
    conservation_before: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    receipt_context = _decision_context(decision, context)
    return {
        "decision_id": decision_id,
        "domain": "s2p",
        "actual_action": actual_action,
        "outcome": outcome,
        "confidence": _decision_confidence(decision),
        "category": _decision_category(decision),
        "recommended_action": _decision_recommended_action(decision),
        "invoice_id": receipt_context.get("invoice_id") or _decision_invoice_id(decision) or None,
        "factor_hash": _receipt_factor_hash(decision),
        "factor_names": list(S2PDomainConfig.factors),
        "factor_vector": _decision_factor_vector(decision),
        "timestamp": _decision_metadata(decision).get("timestamp")
        or _decision_metadata(decision).get("created_at")
        or (decision or {}).get("timestamp")
        or (decision or {}).get("created_at"),
        "conservation_before": _json_safe(conservation_before),
    }


def _enqueue_evidence_receipt_intent(
    *,
    graph_store: Any,
    receipt_intent_id: str,
    domain: str,
    decision_id: str,
    canonical_payload: dict[str, Any],
    actor: str,
    source_route: str,
    metadata: dict[str, Any],
) -> int:
    enqueue = getattr(graph_store, "enqueue_to_outbox", None)
    if not callable(enqueue):
        raise RuntimeError("graph store does not support receipt outbox fallback")
    return int(
        enqueue(
            domain=domain,
            operation_type="append_evidence_receipt",
            target_key=f"{domain}:{receipt_intent_id}",
            payload={
                "receipt_intent_id": receipt_intent_id,
                "domain": domain,
                "decision_id": decision_id,
                "canonical_payload": canonical_payload,
                "actor": actor,
                "source_route": source_route,
                "metadata": metadata,
            },
            causal_decision_id=decision_id,
        )
    )


def _append_evidence_receipt_before_outcome(
    *,
    graph_store: Any,
    decision: dict[str, Any] | None,
    decision_id: str,
    actual_action: str,
    outcome: str,
    actor: str,
    source_route: str,
    conservation_before: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    append = getattr(graph_store, "append_evidence_receipt", None)
    domain = _graph_domain(graph_store)
    receipt_intent_id = f"RCP-{uuid4().hex[:12]}"
    canonical_payload = _evidence_receipt_payload(
        decision=decision,
        decision_id=decision_id,
        actual_action=actual_action,
        outcome=outcome,
        conservation_before=conservation_before,
        context=context,
    )
    metadata = {
        "receipt_intent_id": receipt_intent_id,
        "phase": "pre_outcome",
        "factor_schema_version": "s2p_factor_schema_v2",
    }
    if not callable(append):
        try:
            outbox_id = _enqueue_evidence_receipt_intent(
                graph_store=graph_store,
                receipt_intent_id=receipt_intent_id,
                domain=domain,
                decision_id=decision_id,
                canonical_payload=canonical_payload,
                actor=actor,
                source_route=source_route,
                metadata=metadata,
            )
            return {
                "receipt_intent_id": receipt_intent_id,
                "chain_index": None,
                "payload_hash": None,
                "receipt_queued": True,
                "outbox_id": outbox_id,
            }
        except Exception as outbox_exc:
            raise HTTPException(
                status_code=503,
                detail="Evidence receipt persistence is unavailable",
            ) from outbox_exc

    try:
        chain_index, payload_hash = append(
            receipt_intent_id=receipt_intent_id,
            domain=domain,
            decision_id=decision_id,
            canonical_payload=canonical_payload,
            actor=actor,
            source_route=source_route,
            metadata=metadata,
        )
        return {
            "receipt_intent_id": receipt_intent_id,
            "chain_index": chain_index,
            "payload_hash": payload_hash,
            "receipt_queued": False,
            "outbox_id": None,
        }
    except Exception as append_exc:
        try:
            outbox_id = _enqueue_evidence_receipt_intent(
                graph_store=graph_store,
                receipt_intent_id=receipt_intent_id,
                domain=domain,
                decision_id=decision_id,
                canonical_payload=canonical_payload,
                actor=actor,
                source_route=source_route,
                metadata=metadata,
            )
            log.warning(
                "S2P evidence receipt append failed before outcome; queued outbox_id=%s: %s",
                outbox_id,
                append_exc,
            )
            return {
                "receipt_intent_id": receipt_intent_id,
                "chain_index": None,
                "payload_hash": None,
                "receipt_queued": True,
                "outbox_id": outbox_id,
            }
        except Exception as outbox_exc:
            log.exception("S2P evidence receipt persistence failed before outcome")
            raise HTTPException(
                status_code=503,
                detail="Evidence receipt persistence failed before outcome write",
            ) from outbox_exc


def _decision_context(
    decision: dict[str, Any] | None,
    explicit_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context: dict[str, Any] = {}
    metadata = _decision_metadata(decision)
    factors = decision.get("factors") if isinstance(decision, dict) else {}
    factors = factors if isinstance(factors, dict) else {}

    for key in (
        "invoice_id",
        "source_invoice_id",
        "supplier_id",
        "supplier",
        "supplier_name",
        "commodity",
        "amount",
        "total_amount",
        "invoice_number",
        "po_number",
    ):
        value = metadata.get(key)
        if value is None and isinstance(decision, dict):
            value = decision.get(key)
        if value is not None:
            context[key] = value

    if "supplier" not in context and "supplier_name" in context:
        context["supplier"] = context["supplier_name"]
    if "supplier" not in context and "supplier_id" in context:
        context["supplier"] = context["supplier_id"]
    if "total_amount" not in context and "amount" in context:
        context["total_amount"] = context["amount"]

    for key in (
        "match_status",
        "amount_variance_ratio",
        "duplicate_score",
        "supplier_exception_history",
        "payment_terms_impact",
        "commodity_index_correlation",
        "tax_regulatory_compliance",
    ):
        value = factors.get(key)
        if value is not None:
            context[key] = value

    context.update({key: value for key, value in (explicit_context or {}).items() if value is not None})
    return context


def _record_supplier_profile(
    decision: dict[str, Any] | None,
    payload: dict[str, Any],
    actual_action: str,
    context: dict[str, Any] | None,
) -> None:
    """Best-effort supplier profile update after scorer learning succeeds."""
    try:
        supplier_profile_accumulator.on_decision_verified(
            decision if isinstance(decision, dict) else {},
            {
                "actual_action": actual_action,
                "is_correct": actual_action == _decision_recommended_action(decision),
                "reward": payload.get("reward"),
            },
            context=context,
        )
    except Exception:
        log.exception("Supplier profile accumulator update failed")


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
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


def _trajectory_attr(trajectory: Any, name: str, default: Any) -> Any:
    if isinstance(trajectory, dict):
        return trajectory.get(name, default)
    return getattr(trajectory, name, default)


def _interpret_sdk_iks(iks: float, decisions: int) -> str:
    if decisions <= 0:
        return "Cold start. Awaiting first verified S2P outcomes."
    if iks >= 80:
        return "High institutional knowledge. Scorer well-calibrated."
    if iks >= 50:
        return "Moderate institutional knowledge. Calibration in progress."
    if iks >= 20:
        return "Early learning. Centroids moving from prior."
    return "Cold start. Awaiting additional verified S2P outcomes."


def _iks_status(iks: float, decisions: int) -> str:
    if decisions <= 0:
        return "CALIBRATING"
    if iks >= 80:
        return "ACTIVE"
    if iks >= 20:
        return "LEARNING"
    return "CALIBRATING"


def _iks_from_trajectory(trajectory: Any) -> dict[str, Any]:
    iks = _json_safe_float(_trajectory_attr(trajectory, "current_iks", 0.0))
    decisions_raw = _trajectory_attr(trajectory, "decisions_total", 0)
    try:
        decisions = max(int(decisions_raw), 0)
    except (TypeError, ValueError):
        decisions = 0
    iks_value = iks if iks is not None else 0.0
    return {
        "iks": round(iks_value, 1),
        "d_max": 0.20,
        "mean_drift": 0.0,
        "decisions": decisions,
        "domain": "s2p",
        "status": _iks_status(iks_value, decisions),
        "learning_active": decisions > 0,
        "interpretation": _interpret_sdk_iks(iks_value, decisions),
    }


def _validate_reason_code(outcome: str, reason_code: str | None) -> None:
    if outcome == "override" and not reason_code:
        raise HTTPException(status_code=400, detail="reason_code is required for override outcomes")
    if reason_code and reason_code not in S2PDomainConfig.reason_codes:
        raise HTTPException(
            status_code=400,
            detail=f"reason_code must be one of {S2PDomainConfig.reason_codes}",
        )


def _learn_with_scorer(
    scorer: Any,
    decision_id: str,
    actual_action: str,
    outcome: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        decision = scorer.graph_store.get_decision(decision_id)
    except Exception:
        decision = None
    invoice_id_before = _decision_invoice_id(decision) if isinstance(decision, dict) else ""
    had_invoice_link = _has_decision_invoice_link(scorer.graph_store, decision_id, invoice_id_before)
    learn_context = _decision_context(decision, context)
    with _GRAPH_LINK_ADVISORY_LOCK:
        original_link = getattr(scorer.graph_store, "link_decision_to_entity", None)
        restore_link = False
        try:
            if callable(original_link):
                def _advisory_invoice_link(
                    linked_decision_id: str,
                    entity_id: str,
                    edge_type: str = "DECIDED_ON",
                ) -> None:
                    if (
                        had_invoice_link
                        and str(linked_decision_id) == str(decision_id)
                        and str(entity_id) == str(invoice_id_before)
                        and edge_type == "DECIDED_ON"
                    ):
                        return
                    try:
                        original_link(linked_decision_id, entity_id, edge_type=edge_type)
                    except Exception:
                        log.exception("S2P graph invoice link skipped for decision %s", linked_decision_id)

                scorer.graph_store.link_decision_to_entity = _advisory_invoice_link
                restore_link = True
            result = scorer.learn(
                decision_id,
                actual_action,
                outcome,
                context=learn_context,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown decision: {decision_id}") from exc
        finally:
            if restore_link:
                scorer.graph_store.link_decision_to_entity = original_link
    payload = _json_safe(result)
    if isinstance(payload, dict) and isinstance(decision, dict):
        invoice_id = invoice_id_before
        if invoice_id:
            payload.setdefault("invoice_id", invoice_id)
            if not had_invoice_link:
                _link_decision_to_invoice(scorer.graph_store, decision_id, invoice_id)
    return cast(dict[str, Any], payload)


def _ensure_outcome_decision(scorer: Any, request: "OutcomeRequest") -> None:
    if scorer.graph_store.get_decision(request.decision_id) is not None:
        return
    factors = {
        name: float(request.factor_vector[index])
        for index, name in enumerate(S2PDomainConfig.factors)
    }
    category_index = S2PDomainConfig.get_category_index(request.category)
    recommended_index = S2PDomainConfig.get_action_index(request.predicted_action)
    metadata = {
        "decision_id": request.decision_id,
        "domain": "s2p",
        "entity_id": request.decision_id,
        "category_index": category_index,
        "factor_vector": list(request.factor_vector),
        "recommended_index": recommended_index,
        "probabilities": [
            1.0 if index == recommended_index else 0.0
            for index in range(S2PDomainConfig.n_actions)
        ],
    }
    scorer.graph_store.write_decision(
        domain=_graph_domain(scorer.graph_store),
        category=request.category,
        action=request.predicted_action,
        confidence=1.0,
        factors=factors,
        metadata=metadata,
    )


def _pad_legacy_factor_vector(vector: list[float]) -> list[float]:
    if S2PDomainConfig.n_factors == 8 and len(vector) == 7:
        return [*vector, 0.5]
    return vector


def _request_fields_set(request: ScoreRequest) -> set[str]:
    fields = getattr(request, "model_fields_set", None)
    if fields is not None:
        return set(fields)
    return set(getattr(request, "__fields_set__", set()))


def _invoice_from_request(request: ScoreRequest, fixture_invoice: dict | None) -> dict:
    if fixture_invoice:
        invoice = dict(fixture_invoice)
        invoice["event_id"] = request.event_id
    else:
        invoice = {"event_id": request.event_id, "invoice_id": request.event_id}

    invoice["category"] = request.category
    invoice["amount"] = request.amount
    invoice["supplier_id"] = request.supplier_id
    if request.supplier_name is not None:
        invoice["supplier_name"] = request.supplier_name
    if request.contract_id is not None:
        invoice["contract_id"] = request.contract_id
    request_fields = _request_fields_set(request)
    optional_fields = (
        "approved_categories",
        "supplier_risk_rating",
        "historical_spend_mean",
        "historical_spend_std",
        "vendor_decisions",
        "vendor_approvals",
    )
    for field in optional_fields:
        if fixture_invoice and field not in request_fields:
            continue
        value = getattr(request, field)
        if field == "approved_categories":
            value = value or []
        invoice[field] = value

    explicit_factors = {
        name: getattr(request, name, None)
        for name in S2PDomainConfig.factors
        if getattr(request, name, None) is not None
    }
    if explicit_factors:
        factors = dict(invoice.get("factors") or {})
        factors.update(explicit_factors)
        invoice["factors"] = factors
        for name, value in explicit_factors.items():
            invoice[name] = value
    return invoice


_MANUAL_REVIEW_HOURS = 0.5
_ANALYST_HOURLY_RATE = 80.0


def _invoice_value(invoice: Any) -> float:
    if isinstance(invoice, dict):
        for key in ("amount", "invoice_amount", "total_amount", "value"):
            amount = _to_float(invoice.get(key), 0.0)
            if amount > 0:
                return amount
    for key in ("amount", "invoice_amount", "total_amount", "value"):
        amount = _to_float(getattr(invoice, key, None), 0.0)
        if amount > 0:
            return amount
    return 0.0


def _invoice_category(invoice: Any) -> str:
    if isinstance(invoice, dict):
        return str(invoice.get("category") or "")
    return str(getattr(invoice, "category", "") or "")


def _invoice_variance_pct(invoice: Any) -> float:
    if isinstance(invoice, dict):
        raw_variance = invoice.get("price_variance_pct")
        if raw_variance is None:
            raw_variance = invoice.get("amount_variance_pct")
        if raw_variance is None:
            raw_variance = _to_float(invoice.get("amount_variance_ratio"), 0.0) * 100.0
    else:
        raw_variance = getattr(invoice, "price_variance_pct", None)
        if raw_variance is None:
            raw_variance = getattr(invoice, "amount_variance_pct", None)
        if raw_variance is None:
            raw_variance = _to_float(getattr(invoice, "amount_variance_ratio", 0.0), 0.0) * 100.0
    return round(_to_float(raw_variance, 0.0), 2)


def _computed_cost_of_error(invoice: Any) -> dict[str, Any]:
    category = _invoice_category(invoice)
    similar = [
        item
        for item in load_invoices()
        if (not category or _invoice_category(item) == category)
        and abs(_invoice_variance_pct(item)) > 5.0
    ]
    if not similar:
        similar = [invoice]
    values = [_invoice_value(item) for item in similar]
    values = [value for value in values if value > 0]
    average_value = sum(values) / len(values) if values else _invoice_value(invoice)
    similar_count = max(len(similar), 1)
    analyst_time_cost = similar_count * _MANUAL_REVIEW_HOURS * _ANALYST_HOURLY_RATE
    exposure = similar_count * average_value
    total_cost = exposure + analyst_time_cost
    return {
        "similar_invoice_count": similar_count,
        "average_invoice_value": round(average_value, 2),
        "analyst_time_cost": round(analyst_time_cost, 2),
        "exposure": round(exposure, 2),
        "total_cost": round(total_cost, 2),
        "provenance": "sample",
        "display": (
            f"${total_cost:,.0f} computed exposure "
            f"({similar_count} similar invoices + ${analyst_time_cost:,.0f} analyst time)"
        ),
    }


def compute_threshold_decision(invoice: Any) -> dict[str, Any]:
    """What a rule-based system would decide."""
    variance_pct = _invoice_variance_pct(invoice)
    if variance_pct > 5.0:
        cost = _computed_cost_of_error(invoice)
        return {
            "decision": "REJECT",
            "reason": f"Price variance {variance_pct}% exceeds 5.0% threshold",
            "cost_of_error": cost["display"],
            "cost_of_error_details": cost,
            "provenance": "sample",
            "price_variance_pct": variance_pct,
            "threshold_pct": 5.0,
        }
    return {
        "decision": "APPROVE",
        "reason": "Within threshold",
        "price_variance_pct": variance_pct,
        "threshold_pct": 5.0,
    }


class ScoreRequest(BaseModel):
    event_id: str
    category: str
    amount: float
    supplier_id: str
    supplier_name: Optional[str] = None
    contract_id: Optional[str] = None
    approved_categories: Optional[list[str]] = None
    supplier_risk_rating: float = 0.5
    historical_spend_mean: float = 0.0
    historical_spend_std: float = 1.0
    vendor_decisions: int = 0
    vendor_approvals: int = 0
    match_status: Optional[float] = None
    amount_variance_ratio: Optional[float] = None
    duplicate_score: Optional[float] = None
    supplier_exception_history: Optional[float] = None
    payment_terms_impact: Optional[float] = None
    commodity_index_correlation: Optional[float] = None
    tax_regulatory_compliance: Optional[float] = None
    environmental_risk: Optional[float] = None


class ScoreResponse(BaseModel):
    event_id: str
    category: str
    action: str
    action_index: int
    confidence: float
    probabilities: list[float]
    factor_vector: list[float]
    factor_names: list[str]
    decision_id: str
    process_context: Optional[dict] = None
    active_variant: Optional[dict] = None
    auto_approve: Optional[dict] = None
    novelty_score: Optional[float] = None
    threshold_decision: Optional[dict] = None


@router.post("/score", response_model=S2PScoreResponse)
def score_procurement_event(request: ScoreRequest, http_request: Request) -> dict[str, Any]:
    """
    Score a procurement event and return recommended action.
    POST /api/s2p/score
    """
    if request.category not in S2PDomainConfig.categories:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown category: {request.category}. "
                   f"Valid: {S2PDomainConfig.categories}"
        )

    event = S2PEvent(
        event_id=request.event_id,
        category=request.category,
        amount=request.amount,
        supplier_id=request.supplier_id,
        contract_id=request.contract_id,
        approved_categories=request.approved_categories or [],
        supplier_risk_rating=request.supplier_risk_rating,
        historical_spend_mean=request.historical_spend_mean,
        historical_spend_std=request.historical_spend_std,
        vendor_decisions=request.vendor_decisions,
        vendor_approvals=request.vendor_approvals,
        match_status=request.match_status,
        amount_variance_ratio=request.amount_variance_ratio,
        duplicate_score=request.duplicate_score,
        supplier_exception_history=request.supplier_exception_history,
        payment_terms_impact=request.payment_terms_impact,
        commodity_index_correlation=request.commodity_index_correlation,
        tax_regulatory_compliance=request.tax_regulatory_compliance,
    )

    fixture_invoice = find_invoice(request.event_id)
    invoice = _invoice_from_request(request, fixture_invoice)
    try:
        active_variant = get_active_variant(category=request.category)
    except Exception:
        log.exception("S2P active variant enrichment failed")
        active_variant = None
    lookup_id = invoice.get("invoice_id") or request.event_id
    context = _resolve_graph_context(lookup_id, http_request)
    cross_copilot_signal = _apply_cross_copilot_signal(invoice, request)
    computed_factors = compute_all_factors(invoice, context=context)
    factor_vector = [computed_factors[name] for name in S2PDomainConfig.factors]
    scorer = _sdk_scorer(http_request)

    with get_mutation_lock("s2p"):
        try:
            score_result = scorer.score(
                computed_factors,
                request.category,
                metadata=_invoice_decision_metadata(invoice),
            )
        except AssertionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        core = {
            "event_id": request.event_id,
            "category": request.category,
            "action": score_result.action,
            "action_index": score_result.action_index,
            "confidence": score_result.confidence,
            "probabilities": list(score_result.probabilities),
            "factor_vector": list(factor_vector),
            "factor_names": list(S2PDomainConfig.factors),
            "decision_id": score_result.decision_id,
        }
        conservation_snapshot = _cached_score_conservation_status_only(http_request)
        centroid_snapshot = {}
        try:
            centroid_snapshot = _snapshot_score_centroids(scorer)
            if centroid_snapshot is None:
                centroid_snapshot = {}
        except Exception:
            log.exception("S2P score centroid snapshot skipped")
            centroid_snapshot = {}
        try:
            apply_cache_invalidation_event("s2p", "score")
        except Exception:
            log.exception("S2P score cache invalidation failed")

    conservation_status = conservation_snapshot
    try:
        auto_approve = _should_auto_approve(
            request.category,
            core["confidence"],
            conservation_status,
            core["action"],
        )
        auto_approve["confidence"] = core["confidence"]
        auto_approve["conservation_status"] = conservation_status
        auto_approve["action"] = core["action"]
    except Exception:
        log.exception("S2P auto-approve enrichment failed")
        auto_approve = None

    novelty_score = _record_score_novelty_from_snapshot(factor_vector, request.category, centroid_snapshot, scorer)
    try:
        process_context = _score_process_context_with_signal(cross_copilot_signal)
    except Exception:
        log.exception("S2P process context enrichment failed")
        process_context = None
    try:
        threshold_decision = compute_threshold_decision(invoice)
    except Exception:
        log.exception("S2P threshold decision enrichment failed")
        threshold_decision = None

    try:
        from app.db.neo4j import neo4j_client
        from app.domains.s2p.graph import write_s2p_decision
        write_s2p_decision(
            neo4j_client,
            event_id=core["event_id"],
            category=core["category"],
            action=core["action"],
            action_index=core["action_index"],
            confidence=core["confidence"],
            factor_vector=factor_vector,
            factor_names=S2PDomainConfig.factors,
            supplier_id=request.supplier_id,
            amount=request.amount,
        )
    except Exception as exc:
        logging.getLogger("s2p.score").warning(
            "Primary graph write failed for %s: %s",
            core.get("decision_id", "?"),
            exc,
            exc_info=True,
        )
    _link_decision_to_invoice(scorer.graph_store, core["decision_id"], str(lookup_id))
    if auto_approve is not None:
        _submit_side_effect(lambda: record_auto_approve_decision(dict(auto_approve)))
    _submit_side_effect(
        lambda: _record_score_shadow(
            http_request,
            score_request=request,
            score_result=score_result,
            factor_vector=factor_vector,
            invoice=invoice,
            active_variant=active_variant,
        )
    )

    response = ScoreResponse(
        **core,
        process_context=process_context,
        active_variant=active_variant,
        auto_approve=auto_approve,
        novelty_score=novelty_score,
        threshold_decision=threshold_decision,
    )
    payload = cast(dict[str, Any], _json_safe(response.model_dump()))
    S2PScoreResponse.model_validate(payload)
    return payload


@router.get("/auto-approve/stats", response_model=GenericResponse)
@cached_static("auto-approve-stats", copilot="s2p")
def get_auto_approve_stats_endpoint() -> dict[str, Any]:
    return get_auto_approve_stats()


@router.get("/auto-approve/expansion-proof", response_model=GenericResponse)
def get_auto_approve_expansion_proof(
    http_request: Request,
    category: Optional[str] = None,
) -> dict[str, Any]:
    selected_category = category or S2PDomainConfig.categories[0]
    if selected_category not in AUTO_APPROVE_THRESHOLDS:
        raise HTTPException(status_code=404, detail=f"Unknown category: {selected_category}")
    verified_count, correct_count = _graph_verified_counts(http_request)
    return build_expansion_proof(
        selected_category,
        verified_decisions=verified_count,
        correct_decisions=correct_count,
        conservation_status=_current_conservation_status(http_request),
    )


class OutcomeRequest(BaseModel):
    decision_id: str
    outcome: str            # "confirm" or "override"
    analyst_action: str     # action analyst chose
    analyst_id: str
    factor_vector: list[float]  # original factor vector
    category: str
    predicted_action: str       # original model prediction
    amount: Optional[float] = None
    at_risk: Optional[float] = None
    recovery_pct: Optional[float] = None
    reason_code: Optional[str] = None
    variant_id: Optional[str] = None


class OutcomeResponse(BaseModel):
    decision_id: str
    outcome: str
    learning_applied: bool
    reward: float
    reward_raw: float


class LearnRequest(BaseModel):
    decision_id: str
    actual_action: str
    outcome: str = "confirmed"
    context: Optional[dict] = None
    reason_code: Optional[str] = None
    variant_id: Optional[str] = None


@learn_router.post("/learn", response_model=GenericResponse)
def learn_decision(request: LearnRequest, http_request: Request) -> dict[str, Any]:
    """SDK-shaped learn endpoint backed by the S2P CompoundingScorer."""
    if request.actual_action not in S2PDomainConfig.actions:
        raise HTTPException(
            status_code=422,
            detail=f"actual_action must be one of {S2PDomainConfig.actions}",
        )
    _validate_reason_code(request.outcome, request.reason_code)
    context = dict(request.context or {})
    if request.reason_code:
        context["reason_code"] = request.reason_code
    scorer = _sdk_scorer(http_request)

    with get_mutation_lock("s2p"):
        try:
            decision = scorer.graph_store.get_decision(request.decision_id)
        except Exception:
            decision = None
        category = _decision_category(decision)
        pre_centroid = _read_centroid_for_l5(
            scorer,
            category=category,
            action=request.actual_action,
        )
        conservation_before = _receipt_conservation_snapshot(http_request)
        evidence_receipt = _append_evidence_receipt_before_outcome(
            graph_store=scorer.graph_store,
            decision=decision,
            decision_id=request.decision_id,
            actual_action=request.actual_action,
            outcome=request.outcome,
            actor="api/learn",
            source_route="/api/learn",
            conservation_before=conservation_before,
            context=context,
        )
        payload = _learn_with_scorer(
            scorer,
            request.decision_id,
            request.actual_action,
            request.outcome,
            context,
        )
        _clear_score_conservation_status_cache()
        _persist_l5_centroid_state(
            http_request,
            scorer=scorer,
            category=category,
            actual_action=request.actual_action,
            decision_id=request.decision_id,
            pre_centroid=pre_centroid,
        )
        _persist_l5_conservation_state(http_request, request.decision_id)
        _persist_l5_dk_state(
            http_request,
            decision=decision,
            actual_action=request.actual_action,
            payload=payload,
        )
        apply_cache_invalidation_event("s2p", "learn")
        conservation_after_snapshot = _receipt_conservation_snapshot(http_request)
        payload_snapshot = dict(payload)
        decision_snapshot = copy.deepcopy(decision) if isinstance(decision, dict) else None

    if _outcome_recorded_for_receipt(
        payload_snapshot,
        conservation_before.get("verified_count"),
        conservation_after_snapshot.get("verified_count"),
    ):
        _record_outcome_receipt(
            decision=decision_snapshot,
            payload=payload_snapshot,
            actual_action=request.actual_action,
            reason_code=context.get("reason_code"),
            conservation_before=conservation_before,
            conservation_after=conservation_after_snapshot,
            evidence_receipt=evidence_receipt,
            context=context,
        )
    _record_supplier_profile(decision_snapshot, payload_snapshot, request.actual_action, context)
    _record_evolver_outcome_if_allowed(
        payload_snapshot,
        request.variant_id,
        reward=payload_snapshot.get("reward"),
        category=_decision_category(decision_snapshot) or "",
        http_request=http_request,
    )
    _record_outcome_shadow(
        http_request,
        operation="learn_shadow",
        decision=decision_snapshot,
        decision_id=request.decision_id,
        actual_action=request.actual_action,
        outcome=request.outcome,
        metadata={"reason_code": context.get("reason_code")},
    )
    return payload_snapshot


@router.post("/outcome", response_model=GenericResponse)
def record_outcome(request: OutcomeRequest, http_request: Request) -> dict[str, Any]:
    """
    Record analyst outcome and optionally update centroids.
    POST /api/s2p/outcome
    """
    if request.outcome not in ("confirm", "override"):
        raise HTTPException(status_code=422,
            detail="outcome must be 'confirm' or 'override'")
    _validate_reason_code(request.outcome, request.reason_code)

    if request.analyst_action not in S2PDomainConfig.actions:
        raise HTTPException(status_code=422,
            detail=f"analyst_action must be one of {S2PDomainConfig.actions}")

    request.factor_vector = _pad_legacy_factor_vector(list(request.factor_vector))
    if len(request.factor_vector) != S2PDomainConfig.n_factors:
        raise HTTPException(status_code=422,
            detail=f"factor_vector must contain {S2PDomainConfig.n_factors} values")

    scorer = _sdk_scorer(http_request)
    outcome_context = {
        "amount": request.amount,
        "at_risk": request.at_risk,
        "recovery_pct": request.recovery_pct,
        "reason_code": request.reason_code,
    }

    with get_mutation_lock("s2p"):
        _ensure_outcome_decision(scorer, request)
        try:
            decision = scorer.graph_store.get_decision(request.decision_id)
        except Exception:
            decision = None
        category = _decision_category(decision) or request.category
        pre_centroid = _read_centroid_for_l5(
            scorer,
            category=category,
            action=request.analyst_action,
        )
        invoice_id = _decision_invoice_id(decision)
        outcome_context["invoice_id"] = invoice_id or None
        conservation_before = _receipt_conservation_snapshot(http_request)
        evidence_receipt = _append_evidence_receipt_before_outcome(
            graph_store=scorer.graph_store,
            decision=decision,
            decision_id=request.decision_id,
            actual_action=request.analyst_action,
            outcome=request.outcome,
            actor=request.analyst_id,
            source_route="/api/s2p/outcome",
            conservation_before=conservation_before,
            context=outcome_context,
        )
        try:
            from app.db.neo4j import neo4j_client
            from app.domains.s2p.graph import write_s2p_outcome
            write_s2p_outcome(neo4j_client, request.decision_id,
                request.outcome, request.analyst_action, request.analyst_id)
        except Exception:
            pass  # Neo4j unavailable - outcome still processed

        payload = _learn_with_scorer(
            scorer,
            request.decision_id,
            request.analyst_action,
            request.outcome,
            outcome_context,
        )
        payload["outcome"] = request.outcome
        _clear_score_conservation_status_cache()
        _persist_l5_centroid_state(
            http_request,
            scorer=scorer,
            category=category,
            actual_action=request.analyst_action,
            decision_id=request.decision_id,
            pre_centroid=pre_centroid,
        )
        _persist_l5_conservation_state(http_request, request.decision_id)
        _persist_l5_dk_state(
            http_request,
            decision=decision,
            actual_action=request.analyst_action,
            payload=payload,
        )
        apply_cache_invalidation_event("s2p", "learn")
        conservation_after_snapshot = _receipt_conservation_snapshot(http_request)
        payload_snapshot = dict(payload)
        decision_snapshot = copy.deepcopy(decision) if isinstance(decision, dict) else None

    if _outcome_recorded_for_receipt(
        payload_snapshot,
        conservation_before.get("verified_count"),
        conservation_after_snapshot.get("verified_count"),
    ):
        _record_outcome_receipt(
            decision=decision_snapshot,
            payload=payload_snapshot,
            actual_action=request.analyst_action,
            reason_code=request.reason_code,
            conservation_before=conservation_before,
            conservation_after=conservation_after_snapshot,
            evidence_receipt=evidence_receipt,
            context=outcome_context,
        )
    _record_supplier_profile(decision_snapshot, payload_snapshot, request.analyst_action, outcome_context)
    _record_evolver_outcome_if_allowed(
        payload_snapshot,
        request.variant_id,
        reward=payload_snapshot.get("reward"),
        category=request.category,
        http_request=http_request,
    )
    if request.reason_code:
        payload_snapshot["reason_code"] = request.reason_code
    payload_snapshot["learning_applied"] = payload_snapshot.get("status") != "paused"
    _record_outcome_shadow(
        http_request,
        operation="outcome_shadow",
        decision=decision_snapshot,
        decision_id=request.decision_id,
        actual_action=request.analyst_action,
        outcome=request.outcome,
        metadata={
            "analyst_id": request.analyst_id,
            "reason_code": request.reason_code,
            "invoice_id": invoice_id or None,
        },
    )
    return payload_snapshot


@router.get("/iks", response_model=GenericResponse)
@cached_static("iks", copilot="s2p")
def get_iks(http_request: Request) -> dict[str, Any]:
    """
    GET /api/s2p/iks
    Returns current S2P Institutional Knowledge Score.
    """
    scorer = _sdk_scorer(http_request)
    trajectory = scorer.trajectory()
    return cast(dict[str, Any], _json_safe(_iks_from_trajectory(trajectory)))


@router.get("/learning-gate", response_model=LearningGateResponse)
@cached_static("learning-gate", copilot="s2p")
def get_learning_gate() -> dict:
    """
    GET /api/s2p/learning-gate
    Returns S2P Learning Activation Gate status.
    """
    from app.services.s2p_learning_gate import (
        evaluate_s2p_learning_gate,
        MIN_VERIFIED_DECISIONS,
        MIN_OVERRIDE_PRECISION,
        S2P_SIGMA_GREEN,
        S2P_SIGMA_AMBER,
    )

    verified_decisions = 0
    override_precision = 0.0

    # Neo4jClient.session() is async-only; this sync endpoint keeps the existing
    # cold-start fallback instead of calling an async context manager from sync code.

    gate = evaluate_s2p_learning_gate(
        verified_decisions=verified_decisions,
        override_precision=override_precision,
    )

    return {
        "status":               gate.status,
        "learning_active":      gate.learning_active,
        "verified_decisions":   gate.verified_decisions,
        "override_precision":   gate.override_precision,
        "sigma_max":            gate.sigma_max,
        "reason":               gate.reason,
        "recommendation":       gate.recommendation,
        "gate_opened_at":       gate.gate_opened_at,
        "thresholds": {
            "min_verified_decisions": MIN_VERIFIED_DECISIONS,
            "min_override_precision": MIN_OVERRIDE_PRECISION,
            "sigma_green":            S2P_SIGMA_GREEN,
            "sigma_amber":            S2P_SIGMA_AMBER,
        },
    }
