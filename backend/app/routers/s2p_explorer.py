"""Read-only S2P centroid explorer endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.domains.s2p.config import S2PDomainConfig


router = APIRouter(prefix="/api/s2p/explorer", tags=["s2p-explorer"])


def _get_scorer(http_request: Request) -> Any:
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        raise HTTPException(status_code=500, detail="S2P scorer is not configured")
    return scorer


def _get_config() -> type[S2PDomainConfig]:
    return S2PDomainConfig


def _centroids_from_scorer(scorer: Any) -> Any:
    gae_scorer = getattr(scorer, "gae_scorer", None)
    if gae_scorer is not None and hasattr(gae_scorer, "centroids"):
        return getattr(gae_scorer, "centroids")
    if hasattr(scorer, "centroids"):
        return getattr(scorer, "centroids")
    raise HTTPException(status_code=503, detail="Centroids unavailable")


def _read_centroid(scorer: Any, category: str, action: str) -> list[float]:
    config = _get_config()
    if category not in config.categories:
        raise HTTPException(status_code=404, detail=f"Unknown category: {category}")
    if action not in config.actions:
        raise HTTPException(status_code=404, detail=f"Unknown action: {action}")

    category_index = config.get_category_index(category)
    action_index = config.get_action_index(action)
    centroid = _centroids_from_scorer(scorer)[category_index][action_index]
    if hasattr(centroid, "tolist"):
        values = centroid.tolist()
    else:
        values = list(centroid)
    return [float(value) for value in values]


def _read_dk_weights(scorer: Any) -> list[float] | None:
    for owner in (scorer, getattr(scorer, "gae_scorer", None)):
        if owner is None:
            continue
        for name in ("dk_weights", "precision_weights", "kernel_weights"):
            weights = getattr(owner, name, None)
            if weights is None:
                continue
            if callable(weights):
                weights = weights()
            if hasattr(weights, "tolist"):
                weights = weights.tolist()
            return [float(value) for value in list(weights)]
    return None


def _rounded(values: list[float]) -> list[float]:
    return [round(float(value), 6) for value in values]


def _decision_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _decision_invoice_id(decision: dict[str, Any]) -> str:
    metadata = _decision_metadata(decision)
    for key in ("invoice_id", "source_invoice_id", "entity_id", "decision_id"):
        value = metadata.get(key) or decision.get(key)
        if value:
            return str(value)
    return ""


def _graph_domain(graph_store: Any | None = None) -> str:
    return str(getattr(graph_store, "domain", None) or "s2p")


def _find_scored_decision(scorer: Any, invoice_id: str) -> dict[str, Any] | None:
    graph_store = getattr(scorer, "graph_store", None)
    get_decision = getattr(graph_store, "get_decision", None)
    if callable(get_decision):
        decision = get_decision(invoice_id)
        if isinstance(decision, dict):
            return decision

    get_all_decisions = getattr(graph_store, "get_all_decisions", None)
    if not callable(get_all_decisions):
        return None
    for decision in get_all_decisions(_graph_domain(graph_store)):
        if not isinstance(decision, dict):
            continue
        if _decision_invoice_id(decision) == invoice_id:
            return decision
    return None


def _decision_factor_vector(decision: dict[str, Any]) -> list[float]:
    metadata = _decision_metadata(decision)
    raw_vector = metadata.get("factor_vector")
    if isinstance(raw_vector, list):
        return [float(value) for value in raw_vector]

    factors = decision.get("factors")
    if isinstance(factors, dict):
        return [float(factors.get(name, 0.5)) for name in _get_config().factors]
    return []


def _centroid_rows(scorer: Any) -> list[dict[str, Any]]:
    config = _get_config()
    rows = []
    for category_index, category in enumerate(config.categories):
        for action_index, action in enumerate(config.actions):
            rows.append(
                {
                    "category": category,
                    "category_index": category_index,
                    "action": action,
                    "action_index": action_index,
                    "centroid": _rounded(_read_centroid(scorer, category, action)),
                }
            )
    return rows


@router.get("/export/centroids")
def export_centroids(http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    rows = _centroid_rows(scorer)
    return {
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "tensor_shape": [config.n_categories, config.n_actions, config.n_factors],
        "factors": list(config.factors),
        "categories": list(config.categories),
        "actions": list(config.actions),
        "total_cells": len(rows),
        "centroids": rows,
        "format": "flat",
    }


@router.get("/export/csv")
def export_centroids_csv(http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    header = ["category", "action"] + list(config.factors)
    rows = [
        [row["category"], row["action"], *row["centroid"]]
        for row in _centroid_rows(scorer)
    ]
    return {"header": header, "rows": rows, "total_rows": len(rows)}


@router.get("/centroid/{category}/{action}")
def centroid(category: str, action: str, http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    values = _read_centroid(scorer, category, action)
    return {
        "category": config.get_category_index(category),
        "category_name": category,
        "action": config.get_action_index(action),
        "action_name": action,
        "centroid": _rounded(values),
        "factors": list(config.factors),
    }


@router.get("/drift/{category}")
def drift(category: str, http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    if category not in config.categories:
        raise HTTPException(status_code=404, detail=f"Unknown category: {category}")
    return {
        "category": config.get_category_index(category),
        "category_name": category,
        "factors": list(config.factors),
        "centroids": {
            action: _rounded(_read_centroid(scorer, category, action))
            for action in config.actions
        },
    }


@router.get("/dk-weights")
def dk_weights(http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    weights = _read_dk_weights(scorer)
    if weights is None or len(weights) != config.n_factors:
        return {"factors": list(config.factors), "weights": [], "available": False}
    return {"factors": list(config.factors), "weights": _rounded(weights), "available": True}


@router.get("/contribution")
def contribution(invoice_id: str, http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    decision = _find_scored_decision(scorer, invoice_id)
    if decision is None:
        raise HTTPException(
            status_code=404,
            detail="No score result for invoice_id. Score the invoice first.",
        )

    category = str(decision.get("category") or _decision_metadata(decision).get("category") or "")
    if category not in config.categories:
        raise HTTPException(status_code=404, detail=f"Unknown category: {category}")

    factor_vector = _decision_factor_vector(decision)
    if len(factor_vector) != config.n_factors:
        raise HTTPException(status_code=404, detail="Stored score result has no factor vector")

    contributions = []
    for factor_index, factor in enumerate(config.factors):
        value = float(factor_vector[factor_index])
        contributions.append(
            {
                "factor": factor,
                "factor_index": factor_index,
                "value": round(value, 6),
                "distance_to_actions": {
                    action: round(
                        abs(value - _read_centroid(scorer, category, action)[factor_index]),
                        6,
                    )
                    for action in config.actions
                },
            }
        )

    return {
        "invoice_id": invoice_id,
        "decision_id": decision.get("decision_id"),
        "category": category,
        "scored_action": decision.get("recommended_action") or decision.get("action"),
        "confidence": decision.get("confidence"),
        "contributions": contributions,
    }
