"""S2P centroid explorer endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import math
import uuid
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
import numpy as np

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.models.responses import ExplorerCentroidResponse, GenericResponse
from app.routers.s2p import _reject_red_write, _score_write_governance
from copilot_sdk.graph.protocol import GraphStore


router = APIRouter(prefix="/api/s2p/explorer", tags=["s2p-explorer"])


def _get_scorer(http_request: Request) -> Any:
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        raise HTTPException(status_code=500, detail="S2P scorer is not configured")
    return scorer


def _get_config() -> type[S2PDomainConfig]:
    return cast(type[S2PDomainConfig], S2PDomainConfig)


def _get_graph_reader(http_request: Request, scorer: Any) -> S2PGraphReader:
    state = getattr(http_request.app, "state", None)
    reader = getattr(state, "s2p_graph_reader", None)
    graph_store = getattr(scorer, "graph_store", None)
    if isinstance(reader, S2PGraphReader) and reader.store is graph_store:
        return reader
    if graph_store is None:
        raise HTTPException(status_code=503, detail="S2P graph reader unavailable")
    return S2PGraphReader(store=graph_store)


def _gae_scorer_from_scorer(scorer: Any) -> Any:
    gae_scorer = getattr(scorer, "gae_scorer", None)
    if gae_scorer is None:
        raise HTTPException(status_code=503, detail="GAE scorer is unavailable")
    return gae_scorer


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
            return _pad_legacy_vector([float(value) for value in list(weights)], S2PDomainConfig.n_factors)
    return None


def _rounded(values: list[float]) -> list[float]:
    return [round(float(value), 6) for value in values]


def _centroid_shape(centroids: Any) -> list[int]:
    if not isinstance(centroids, list):
        return []
    shape: list[int] = []
    current = centroids
    while isinstance(current, list):
        shape.append(len(current))
        if not current:
            break
        first = current[0]
        if any(not isinstance(item, list) for item in current):
            if any(isinstance(item, list) for item in current):
                return []
            return shape
        first_shape = _centroid_shape(first)
        for item in current[1:]:
            if _centroid_shape(item) != first_shape:
                return []
        current = first
    return shape


def _pad_legacy_vector(vector: list[float], expected_len: int) -> list[float]:
    if expected_len == 8 and len(vector) == 7:
        return [*vector, 0.5]
    return vector


def _pad_legacy_centroids(centroids: list, config: type[S2PDomainConfig]) -> list:
    if config.n_factors == 8 and len(centroids) == config.n_categories:
        padded = []
        changed = False
        for category in centroids:
            if not isinstance(category, list) or len(category) != config.n_actions:
                return centroids
            action_rows = []
            for vector in category:
                if not isinstance(vector, list):
                    return centroids
                migrated = _pad_legacy_vector(vector, config.n_factors)
                action_rows.append(migrated)
                changed = changed or migrated is not vector
            padded.append(action_rows)
        if changed:
            return padded
    return centroids


def _validate_centroid_values(centroids: list) -> str | None:
    def walk(value: Any, path: str) -> str | None:
        if isinstance(value, list):
            for index, item in enumerate(value):
                error = walk(item, f"{path}[{index}]")
                if error:
                    return error
            return None
        if value is None:
            return f"{path} is null"
        if isinstance(value, bool):
            return f"{path} must be numeric, not boolean"
        if not isinstance(value, (int, float)):
            return f"{path} must be numeric"
        numeric = float(value)
        if not math.isfinite(numeric):
            return f"{path} must be finite"
        if numeric < 0.0 or numeric > 1.0:
            return f"{path} must be between 0.0 and 1.0"
        return None

    return walk(centroids, "centroids")


def _copy_nested_float_centroids(centroids: list) -> list:
    return [
        _copy_nested_float_centroids(item) if isinstance(item, list) else float(item)
        for item in centroids
    ]


def _current_conservation_status(http_request: Request) -> str:
    try:
        from gae.calibration import conservation_status

        state = getattr(http_request.app, "state", None)
        scorer = getattr(state, "scorer", None)
        reader = _get_graph_reader(http_request, scorer)
        verified_count = int(reader.count_verified())
        correct_count = int(reader.count_correct())
        total_decisions = int(reader.count_verified_decisions())
        check = conservation_status(
            verified_count=max(verified_count, 0),
            correct_count=max(correct_count, 0),
            total_decisions=max(total_decisions, 0),
            penalty_ratio=float(getattr(_get_config(), "penalty_ratio", 5.0)),
        )
        return str(check.status).upper()
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph query failed") from exc
    except Exception:
        return "UNKNOWN"


def _checkpoint_imported_centroids(
    http_request: Request,
    checkpoint_id: str,
    centroids: np.ndarray,
) -> bool:
    state = getattr(http_request.app, "state", None)
    graph_store = getattr(state, "graph_store", None)
    if graph_store is None:
        scorer = getattr(state, "scorer", None)
        graph_store = getattr(scorer, "graph_store", None)
    if not isinstance(graph_store, GraphStore):
        return False
    config = _get_config()
    graph_store.save_centroids(
        _graph_domain(graph_store),
        "import",
        centroids,
        metadata={
            "checkpoint_id": checkpoint_id,
            "source": "s2p_explorer_import",
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "shape": [config.n_categories, config.n_actions, config.n_factors],
            "categories": list(config.categories),
            "actions": list(config.actions),
            "factors": list(config.factors),
        },
        decision_id=checkpoint_id,
    )
    return True


def _uniform_ranked(factors: list[str]) -> list[dict[str, Any]]:
    weight = round(1.0 / len(factors), 6) if factors else 0.0
    return [
        {"factor": factor, "weight": weight, "source": "uniform"}
        for factor in factors
    ]


def _safe_read_dk_weights(scorer: Any, expected_len: int) -> list[float] | None:
    for owner in (scorer, getattr(scorer, "gae_scorer", None)):
        if owner is None:
            continue
        for name in ("dk_weights", "precision_weights", "kernel_weights"):
            try:
                weights = getattr(owner, name, None)
            except Exception:
                continue
            if weights is None:
                continue
            if callable(weights):
                try:
                    weights = weights()
                except Exception:
                    continue
            if weights is None:
                continue
            if isinstance(weights, (dict, str, bytes)):
                return None
            if hasattr(weights, "tolist"):
                try:
                    weights = weights.tolist()
                except Exception:
                    return None
            try:
                values = [float(value) for value in list(weights)]
            except Exception:
                return None
            values = _pad_legacy_vector(values, expected_len)
            if len(values) != expected_len:
                return None
            return values
    return None


def _sigma_ranked_fallback(
    scorer: Any,
    config: Any,
    factors: list[str],
) -> list[dict[str, Any]]:
    centroids = None
    for owner in (scorer, getattr(scorer, "gae_scorer", None), getattr(scorer, "_scorer", None)):
        if owner is None:
            continue
        for name in ("centroids", "mu", "_mu"):
            centroids = getattr(owner, name, None)
            if centroids is not None:
                break
        if centroids is not None:
            break

    if centroids is None:
        return _uniform_ranked(factors)

    try:
        import numpy as np

        values = np.asarray(centroids, dtype=float)
        if values.ndim < 2 or values.shape[-1] != int(config.n_factors):
            return _uniform_ranked(factors)
        rows = values.reshape(-1, values.shape[-1])
        variances = np.var(rows, axis=0).tolist()
    except Exception:
        return _uniform_ranked(factors)

    if len(variances) != len(factors):
        return _uniform_ranked(factors)
    return [
        {
            "factor": factor,
            "weight": round(float(variances[index]), 6),
            "source": "centroid_variance",
        }
        for index, factor in enumerate(factors)
    ]


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


def _find_scored_decision(
    reader: S2PGraphReader,
    invoice_id: str,
) -> dict[str, Any] | None:
    decision = reader.get_decision(invoice_id)
    if isinstance(decision, dict):
        return decision

    for decision in reader.get_all_decisions():
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


@router.get("/export/centroids", response_model=GenericResponse)
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


@router.get("/export/csv", response_model=GenericResponse)
def export_centroids_csv(http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    header = ["category", "action"] + list(config.factors)
    rows = [
        [row["category"], row["action"], *row["centroid"]]
        for row in _centroid_rows(scorer)
    ]
    return {"header": header, "rows": rows, "total_rows": len(rows)}


@router.post("/import/centroids", response_model=GenericResponse)
def import_centroids(payload: dict[str, Any], http_request: Request) -> dict[str, Any]:
    config = _get_config()
    if "centroids" not in payload:
        raise HTTPException(status_code=400, detail="Missing centroids")
    centroids = payload["centroids"]
    if not isinstance(centroids, list):
        raise HTTPException(status_code=400, detail="centroids must be a nested list")
    centroids = _pad_legacy_centroids(centroids, config)

    expected_shape = [config.n_categories, config.n_actions, config.n_factors]
    shape = _centroid_shape(centroids)
    if shape != expected_shape:
        raise HTTPException(
            status_code=400,
            detail=f"centroids shape must be {expected_shape}; got {shape or 'ragged'}",
        )

    validation_error = _validate_centroid_values(centroids)
    if validation_error:
        raise HTTPException(status_code=400, detail=validation_error)

    conservation_status = _current_conservation_status(http_request)
    governance = _score_write_governance(http_request)
    _reject_red_write(governance)
    if conservation_status != "GREEN":
        raise HTTPException(
            status_code=409,
            detail=f"Centroid import requires GREEN conservation; got {conservation_status}",
        )

    scorer = _get_scorer(http_request)
    gae_scorer = _gae_scorer_from_scorer(scorer)
    previous_centroids = np.array(gae_scorer.centroids, dtype=float, copy=True)
    imported = np.asarray(_copy_nested_float_centroids(centroids), dtype=float)
    checkpoint_id = f"CKP-IMPORT-{uuid.uuid4()}"

    gae_scorer.centroids = imported
    try:
        checkpoint_saved = _checkpoint_imported_centroids(
            http_request,
            checkpoint_id,
            imported,
        )
    except Exception as exc:
        gae_scorer.centroids = previous_centroids
        raise HTTPException(
            status_code=500,
            detail=f"Centroid import checkpoint failed: {exc}",
        ) from exc

    if not checkpoint_saved:
        gae_scorer.centroids = previous_centroids
        raise HTTPException(
            status_code=503,
            detail="Centroid checkpoint persistence is unavailable",
        )

    return {
        "imported": True,
        "checkpoint_id": checkpoint_id,
        "checkpoint_saved": True,
        "shape": expected_shape,
        "n_cells": config.n_categories * config.n_actions,
        "n_values": config.n_categories * config.n_actions * config.n_factors,
        "conservation_status": conservation_status,
        "evidence_tier": governance["evidence_tier"],
    }


@router.get("/centroid/{category}/{action}", response_model=ExplorerCentroidResponse)
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


@router.get("/drift/{category}", response_model=GenericResponse)
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


@router.get("/dk-weights", response_model=GenericResponse)
def dk_weights(http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    weights = _read_dk_weights(scorer)
    if weights is None or len(weights) != config.n_factors:
        return {"factors": list(config.factors), "weights": [], "available": False}
    return {"factors": list(config.factors), "weights": _rounded(weights), "available": True}


@router.get("/ranking", response_model=GenericResponse)
def factor_ranking(http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    factors = list(config.factors)
    n_factors = len(factors)
    weights = _safe_read_dk_weights(scorer, n_factors)

    if weights is not None:
        ranked_raw = [
            {
                "factor": factor,
                "weight": round(float(weights[index]), 6),
                "source": "dk_weights",
            }
            for index, factor in enumerate(factors)
        ]
    else:
        ranked_raw = _sigma_ranked_fallback(scorer, config, factors)

    ranked = sorted(ranked_raw, key=lambda item: (item["weight"], item["factor"]))
    ranked = [
        {
            **item,
            "rank": index,
        }
        for index, item in enumerate(ranked, start=1)
    ]
    swap = ranked[0] if ranked else {"factor": "", "weight": 0.0, "source": "uniform"}
    rationale = (
        f"{swap['factor']} is the swap candidate because it has the lowest "
        f"discriminatory weight ({swap['weight']}) among the S2P factors."
    )
    return {
        "factors": factors,
        "ranked": ranked,
        "swap_candidate": swap["factor"],
        "swap_candidate_weight": swap["weight"],
        "rationale": rationale,
        "weight_source": swap["source"],
        "n_factors": n_factors,
    }


@router.get("/contribution", response_model=GenericResponse)
def contribution(invoice_id: str, http_request: Request) -> dict[str, Any]:
    scorer = _get_scorer(http_request)
    config = _get_config()
    reader = _get_graph_reader(http_request, scorer)
    try:
        decision = _find_scored_decision(reader, invoice_id)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph query failed") from exc
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
