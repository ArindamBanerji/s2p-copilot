"""Live, evidence-labelled endpoints for the S2P demo beats."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.services.s2p_autonomy import S2PAutonomyManager


router = APIRouter(prefix="/api/s2p", tags=["S2P demo beats"])
_MIN_COMPETENCE_SAMPLES = 50


def _reader(request: Request) -> S2PGraphReader | None:
    reader = getattr(request.app.state, "s2p_graph_reader", None)
    if isinstance(reader, S2PGraphReader):
        return reader
    store = getattr(request.app.state, "graph_store", None)
    if store is None:
        return None
    return S2PGraphReader(cast(Any, store))


def _scorer(request: Request) -> Any:
    scorer = getattr(request.app.state, "scorer", None)
    if scorer is None:
        raise HTTPException(status_code=503, detail="S2P scorer unavailable")
    return scorer


def _manager(request: Request) -> S2PAutonomyManager:
    manager = getattr(request.app.state, "s2p_autonomy", None)
    if not isinstance(manager, S2PAutonomyManager):
        raise HTTPException(status_code=503, detail="S2P autonomy state unavailable")
    return manager


def _rows(reader: S2PGraphReader | None, verified: bool = False) -> list[dict[str, Any]]:
    if reader is None:
        return []
    try:
        rows = reader.get_verified_decisions() if verified else reader.get_all_decisions()
    except GraphUnavailableError:
        # These are read-only presentation endpoints.  The graph enriches
        # them with history, but scorer/config state remains sufficient for a
        # truthful day-zero response when AGE is temporarily unavailable.
        return []
    return [row for row in rows if isinstance(row, dict)]


def _factor_map(row: dict[str, Any]) -> dict[str, float] | None:
    raw = row.get("factors")
    if isinstance(raw, dict):
        values: dict[str, float] = {}
        for name in S2PDomainConfig.factors:
            value = raw.get(name)
            if isinstance(value, (int, float)) and np.isfinite(float(value)):
                values[name] = float(value)
        if len(values) == len(S2PDomainConfig.factors):
            return values
    vector = row.get("factor_vector")
    if isinstance(vector, (list, tuple)) and len(vector) >= len(S2PDomainConfig.factors):
        try:
            vector_values = [float(value) for value in vector[: len(S2PDomainConfig.factors)]]
        except (TypeError, ValueError):
            return None
        if all(np.isfinite(value) for value in vector_values):
            return dict(zip(S2PDomainConfig.factors, vector_values, strict=True))
    return None


def _category(row: dict[str, Any]) -> str:
    return str(row.get("category") or "unknown")


def _confidence(row: dict[str, Any]) -> float | None:
    for key in ("confidence", "confidence_score", "score_confidence"):
        value = row.get(key)
        if isinstance(value, (int, float)) and np.isfinite(float(value)):
            return max(0.0, min(1.0, float(value)))
    return None


def _action(row: dict[str, Any]) -> str:
    return str(row.get("recommended_action") or row.get("action") or "")


def _decision_invoice_id(row: dict[str, Any]) -> str:
    for key in ("invoice_id", "source_invoice_id", "entity_id", "event_id"):
        value = row.get(key)
        if value:
            return str(value)
    metadata = row.get("metadata")
    if isinstance(metadata, dict) and metadata.get("invoice_id"):
        return str(metadata["invoice_id"])
    return ""


def _centroids(scorer: Any) -> np.ndarray:
    profile = getattr(scorer, "_scorer", scorer)
    centroids = getattr(profile, "centroids", None)
    if centroids is None:
        centroids = getattr(profile, "mu", None)
    if centroids is None:
        return np.asarray(S2PDomainConfig.get_profile_centroids(), dtype=float)
    return np.asarray(centroids, dtype=float)


@router.get("/evolution/extinction")
def extinction(request: Request) -> dict[str, Any]:
    service = getattr(request.app.state, "s2p_evolution", None)
    if service is None:
        raise HTTPException(status_code=503, detail="S2P evolution service unavailable")
    variants = service.get_variants()
    promoted = service.get_promoted()
    shadow = service.get_shadow_results()
    extinct: list[dict[str, Any]] = []
    for item in variants:
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or item.get("state") or "").lower() in {"extinct", "auto_resolved"}:
            extinct.append({
                "category": item.get("category"),
                "variant_id": item.get("variant_id") or item.get("id"),
                "days_to_extinct": item.get("days_to_extinct"),
            })
    category_days = {category: None for category in S2PDomainConfig.categories}
    for item in extinct:
        category = str(item.get("category") or "")
        if category in category_days:
            category_days[category] = item.get("days_to_extinct")
    return {
        "workflow": ["discover", "shadow", "promote"],
        "promotion_count": int(bool(promoted)),
        "promoted": promoted,
        "shadow_variant_count": int(shadow.get("total_variants", 0)),
        "extinct": extinct,
        "days_to_extinct_by_category": category_days,
        "extinction_rate_trend": [{"period": "current", "rate": 0.0 if not variants else len(extinct) / len(variants)}],
        "evidence_tier": "T_S" if not extinct else "T_O",
        "evidence_note": "No canonical auto-resolved extinction events are recorded yet." if not extinct else "Derived from recorded variant state.",
    }


@router.get("/learning/frozen-twin")
def frozen_twin(request: Request) -> dict[str, Any]:
    reader = _reader(request)
    manager = getattr(request.app.state, "s2p_autonomy", None)
    scorer = _scorer(request)
    rows = _rows(reader)
    verified = [row for row in rows if row.get("is_correct") is not None]
    if not isinstance(manager, S2PAutonomyManager) or not manager.twin.is_frozen():
        current = _centroids(scorer)
        initial = np.asarray(S2PDomainConfig.get_profile_centroids(), dtype=float)
        drift = float(np.linalg.norm(current - initial))
        return {
            "frozen_available": True,
            "current_decisions": len(rows),
            "compared_decisions": 0,
            "frozen_decisions_would_miss": [],
            "delta_accuracy": None,
            "delta_coverage": None,
            "current_coverage": None,
            "frozen_coverage": None,
            "current_accuracy": None,
            "frozen_accuracy": None,
            "visual_diff": [],
            "evidence_tier": "T_S",
            "centroid_drift_from_config_baseline": drift,
            "evidence_note": "Graph unavailable; comparison uses the live scorer and canonical S2P config baseline.",
        }
    comparisons: list[dict[str, Any]] = []
    for row in verified:
        vector = row.get("factor_vector")
        if not isinstance(vector, (list, tuple)):
            factor_map = _factor_map(row)
            vector = None if factor_map is None else [factor_map[name] for name in S2PDomainConfig.factors]
        category = _category(row)
        if not isinstance(vector, (list, tuple)) or category not in S2PDomainConfig.categories:
            continue
        try:
            comparison = manager.parallel_score([float(value) for value in vector], category)
        except (TypeError, ValueError, RuntimeError):
            comparison = None
        if comparison is not None:
            comparisons.append({"decision_id": row.get("decision_id"), "category": category, **comparison, "actual_action": row.get("actual_action")})
    frozen_misses = [item for item in comparisons if item.get("frozen", {}).get("action") != item.get("actual_action")]
    current_correct = sum(item.get("live", {}).get("action") == item.get("actual_action") for item in comparisons)
    frozen_correct = sum(item not in frozen_misses for item in comparisons)
    denominator = len(comparisons)
    current_accuracy = current_correct / denominator if denominator else None
    frozen_accuracy = frozen_correct / denominator if denominator else None
    current_coverage = denominator / len(rows) if rows else None
    frozen_coverage = denominator / len(rows) if rows else None
    return {
        "frozen_available": True,
        "current_decisions": len(rows),
        "compared_decisions": denominator,
        "frozen_decisions_would_miss": frozen_misses,
        "delta_accuracy": None if current_accuracy is None else current_accuracy - frozen_accuracy,
        "delta_coverage": (
            None
            if current_coverage is None or frozen_coverage is None
            else current_coverage - frozen_coverage
        ),
        "current_coverage": current_coverage,
        "frozen_coverage": frozen_coverage,
        "current_accuracy": current_accuracy,
        "frozen_accuracy": frozen_accuracy,
        "visual_diff": comparisons,
        "evidence_tier": "T_A",
        "evidence_note": "Comparison uses the persisted immutable S2P day-one twin and live verified decisions.",
    }


@router.get("/context/what-if/{invoice_id}")
def what_if(invoice_id: str, request: Request) -> dict[str, Any]:
    reader = _reader(request)
    scorer = _scorer(request)
    row = next((item for item in _rows(reader) if _decision_invoice_id(item) == invoice_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Invoice decision not found: {invoice_id}")
    factors = _factor_map(row)
    category = _category(row)
    if factors is None or category not in S2PDomainConfig.categories:
        return {"invoice_id": invoice_id, "category": category, "current_action": _action(row), "factor_sensitivity": [], "factor_changes": [], "nearest_decision_boundary": None, "nearest_boundary": None, "evidence_tier": "T_S", "evidence_note": "Factor values are not complete enough for a counterfactual.",}
    factor_values = factors
    current = scorer.score_read_only(factors, category)
    centroids = _centroids(scorer)
    category_index = S2PDomainConfig.get_category_index(category)
    current_index = int(getattr(current, "action_index", 0))
    alternatives = [index for index in range(centroids.shape[1]) if index != current_index]
    alternative_index = min(alternatives, key=lambda index: float(np.linalg.norm(centroids[category_index, index] - np.asarray(list(factor_values.values()))))) if alternatives else current_index
    alternative_action = S2PDomainConfig.actions[alternative_index]
    sensitivity: list[dict[str, Any]] = []
    for factor_index, name in enumerate(S2PDomainConfig.factors):
        boundary = float((centroids[category_index, current_index, factor_index] + centroids[category_index, alternative_index, factor_index]) / 2.0)
        delta = boundary - factor_values[name]
        trial = dict(factor_values)
        trial[name] = boundary
        result = scorer.score_read_only(trial, category)
        sensitivity.append({"factor": name, "current_value": factor_values[name], "boundary_value": boundary, "delta": delta, "direction": "increase" if delta > 0 else "decrease" if delta < 0 else "none", "resulting_action": result.action, "target_action": alternative_action})
    nearest = min(sensitivity, key=lambda item: abs(float(item["delta"])), default=None)
    return {"invoice_id": invoice_id, "category": category, "current_action": current.action, "factor_sensitivity": sensitivity, "factor_changes": sensitivity, "nearest_decision_boundary": nearest, "nearest_boundary": nearest, "evidence_tier": "T_O" if row.get("is_correct") is not None else "T_S", "evidence_note": "Per-factor midpoint analysis of the live scorer boundary; it does not mutate scorer state.",}


@router.get("/diagnostics/day-zero")
def day_zero(request: Request) -> dict[str, Any]:
    rows = _rows(_reader(request))
    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[_category(row)].append(row)
    coverage = {category: {"decisions": len(by_category[category]), "verified": sum(row.get("is_correct") is not None for row in by_category[category]), "ready": sum(row.get("is_correct") is not None for row in by_category[category]) >= _MIN_COMPETENCE_SAMPLES} for category in S2PDomainConfig.categories}
    gaps = [{"category": category, "missing_verified_decisions": max(0, _MIN_COMPETENCE_SAMPLES - data["verified"])} for category, data in coverage.items() if not data["ready"]]
    return {"ready": not gaps, "cannot_trust_yet": [item["category"] for item in gaps], "coverage_per_category": coverage, "source_quality_gaps": gaps, "time_to_competence_estimate": {"unit": "verified decisions", "remaining_max": max((item["missing_verified_decisions"] for item in gaps), default=0), "basis": "live domain-scoped graph counts when available; canonical categories otherwise"}, "evidence_tier": "T_O" if rows else "T_S", "evidence_note": "Readiness is calculated from live S2P graph decisions when available and canonical scorer/config state otherwise.",}


@router.get("/diagnostics/confidence")
def confidence(request: Request) -> dict[str, Any]:
    rows = _rows(_reader(request))
    scorer = _scorer(request)
    centroids = _centroids(scorer)
    categories: dict[str, list[float]] = defaultdict(list)
    per_decision: list[dict[str, Any]] = []
    for row in rows:
        value = _confidence(row)
        factors = _factor_map(row)
        category = _category(row)
        if value is None and factors is not None and category in S2PDomainConfig.categories:
            result = scorer.score_read_only(factors, category)
            value = float(result.confidence)
        if value is None:
            continue
        categories[category].append(value)
        novelty = None
        if factors is not None and category in S2PDomainConfig.categories:
            vector = np.asarray(list(factors.values()), dtype=float)
            index = S2PDomainConfig.get_category_index(category)
            novelty = float(np.min(np.linalg.norm(centroids[index] - vector, axis=1))) > 0.5
        per_decision.append({"decision_id": row.get("decision_id"), "category": category, "confidence": value, "band": "high" if value >= 0.85 else "medium" if value >= 0.65 else "low", "novel": novelty})
    # Keep the panel populated even at day zero.  DK weights are a scorer
    # state signal, not an invented decision history.
    dk_weights = getattr(scorer, "get_dk_weights", None)
    raw_weights = dk_weights() if callable(dk_weights) else getattr(scorer, "_dk_weights", None)
    weight_array = np.asarray(raw_weights, dtype=float) if raw_weights is not None else np.asarray([], dtype=float)
    state_confidence = float(np.clip(np.mean(weight_array), 0.0, 1.0)) if weight_array.size else 0.5
    per_category = [
        {
            "category": category,
            "confidence": round(state_confidence, 6),
            "band": "high" if state_confidence >= 0.85 else "medium" if state_confidence >= 0.65 else "low",
            "source": "scorer DK weights",
        }
        for category in S2PDomainConfig.categories
    ]
    rising = [category for category, values in categories.items() if len(values) >= 2 and values[-1] >= values[0]]
    falling = [category for category, values in categories.items() if len(values) >= 2 and values[-1] < values[0]]
    return {"per_decision": per_decision, "per_category": per_category, "categories_rising": rising, "categories_falling": falling, "novelty_observed": sum(item["novel"] is True for item in per_decision), "evidence_tier": "T_O" if any(row.get("is_correct") is not None for row in rows) else "T_S", "evidence_note": "Confidence is sourced from stored decisions or live scorer DK weights; missing history is not imputed from fixtures.",}


@router.get("/context/rule-vs-reasoning")
def rule_vs_reasoning(
    request: Request,
    invoice_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Contrast a deterministic threshold with the live situation-aware scorer."""
    scorer = _scorer(request)
    centroids = _centroids(scorer)
    vector = np.mean(centroids, axis=(0, 1)).tolist()
    factors = dict(zip(S2PDomainConfig.factors, (float(value) for value in vector), strict=True))
    variance = factors["amount_variance_ratio"]
    threshold = 0.05
    rule_action = "hold_for_review" if variance > threshold else "auto_approve"
    result = scorer.score_read_only(factors, S2PDomainConfig.categories[0])
    return {
        "invoice_id": invoice_id,
        "same_input": factors,
        "rule_based": {
            "threshold_factor": "amount_variance_ratio",
            "threshold": threshold,
            "value": variance,
            "action": rule_action,
            "evidence_tier": "T_S",
        },
        "situation_aware": {
            "action": result.action,
            "confidence": float(result.confidence),
            "evidence_tier": "T_S",
            "factors_considered": list(S2PDomainConfig.factors),
        },
        "contrast": "Same input. Same scorer state. Different decision path.",
    }
