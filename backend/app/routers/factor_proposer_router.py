"""S2P factor proposer endpoints."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import GraphUnavailableError, S2PGraphReader
from app.models.responses import GenericResponse
from app.routers.s2p_explorer import _decision_factor_vector, _graph_domain, _read_dk_weights
from app.services.factor_proposer import FactorProposer
from app.services.financial_impact import limit_recent_decisions


router = APIRouter(prefix="/api/s2p/factors", tags=["s2p-factor-proposer"])

_FACTOR_ANALYSIS_SNAPSHOT: dict[str, Any] | None = None
_FACTOR_RECOMMENDATIONS_SNAPSHOT: dict[str, Any] | None = None
_FACTOR_SNAPSHOT_SCORER: Any | None = None


class FactorProposalRequest(BaseModel):
    factor: str
    candidates: list[str] = []


@router.get("/analysis", response_model=GenericResponse)
def factor_analysis(request: Request) -> dict[str, Any]:
    _ensure_factor_snapshots(request)
    return dict(_FACTOR_ANALYSIS_SNAPSHOT or {})


@router.get("/recommendations", response_model=GenericResponse)
def factor_recommendations(request: Request) -> dict[str, Any]:
    _ensure_factor_snapshots(request)
    return dict(_FACTOR_RECOMMENDATIONS_SNAPSHOT or {})


@router.post("/propose", response_model=GenericResponse)
def propose_factor(payload: FactorProposalRequest, request: Request) -> dict[str, Any]:
    try:
        return _proposer(request).propose_replacement(payload.factor, payload.candidates)
    except GraphUnavailableError as exc:
        raise HTTPException(status_code=503, detail="S2P graph unavailable for factor analysis") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _proposer(request: Request) -> FactorProposer:
    factors = list(S2PDomainConfig.factors)
    scorer = getattr(request.app.state, "scorer", None)
    return _proposer_for_scorer(scorer, factors)


def _proposer_for_scorer(scorer: Any, factors: list[str]) -> FactorProposer:
    weights = _dk_weight_map(scorer, factors)
    return FactorProposer(weights, _factor_stats(scorer, factors))


def _dk_weight_map(scorer: Any, factors: list[str]) -> dict[str, float]:
    weights = _read_dk_weights(scorer)
    if weights is not None and len(weights) == len(factors) - 1 and len(factors) == 8:
        weights = [*weights, 0.04]
    if weights is not None and len(weights) == len(factors):
        return {factor: float(weights[index]) for index, factor in enumerate(factors)}
    return {
        factor: weight
        for factor, weight in zip(
            factors,
            [0.22, 0.18, 0.16, 0.10, 0.09, 0.08, 0.04, 0.04],
        )
    }


def _factor_stats(scorer: Any, factors: list[str]) -> dict[str, dict[str, float]]:
    live = _live_factor_stats(scorer, factors)
    defaults = _fallback_factor_stats()
    if not live:
        return {factor: defaults.get(factor, {"variance": 0.30, "outcome_corr": 0.05}) for factor in factors}
    return {factor: live.get(factor, defaults.get(factor, {"variance": 0.30, "outcome_corr": 0.05})) for factor in factors}


def _live_factor_stats(scorer: Any, factors: list[str]) -> dict[str, dict[str, float]]:
    graph_store = getattr(scorer, "graph_store", None)
    if graph_store is None:
        raise GraphUnavailableError("S2P graph reader unavailable")
    reader = S2PGraphReader(store=graph_store)
    rows = limit_recent_decisions(
        row for row in reader.get_all_decisions() if isinstance(row, dict)
    )
    vectors: list[list[float]] = []
    outcomes: list[float] = []
    for row in rows:
        vector = _decision_factor_vector(row)
        if len(vector) != len(factors):
            continue
        vectors.append(vector)
        outcomes.append(1.0 if _row_correct(row) else 0.0)
    if len(vectors) < 2:
        return {}
    stats: dict[str, dict[str, float]] = {}
    for index, factor in enumerate(factors):
        values = [vector[index] for vector in vectors]
        stats[factor] = {
            "variance": _variance(values),
            "outcome_corr": _correlation(values, outcomes),
        }
    return stats


def warm_factor_snapshots(scorer: Any) -> None:
    """Materialize factor analysis after seeded scorer state is available."""
    global _FACTOR_ANALYSIS_SNAPSHOT
    global _FACTOR_RECOMMENDATIONS_SNAPSHOT
    global _FACTOR_SNAPSHOT_SCORER

    recommendations = _proposer_for_scorer(scorer, list(S2PDomainConfig.factors)).analyze()
    factors = [item.to_dict() for item in recommendations]
    _FACTOR_ANALYSIS_SNAPSHOT = {
        "factors": factors,
        "count": len(factors),
        "advisory": True,
    }
    recommended = [item for item in factors if item["verdict"] == "replace_candidate"]
    _FACTOR_RECOMMENDATIONS_SNAPSHOT = {
        "recommendations": recommended,
        "count": len(recommended),
        "advisory": True,
    }
    _FACTOR_SNAPSHOT_SCORER = scorer


def reset_factor_snapshots() -> None:
    global _FACTOR_ANALYSIS_SNAPSHOT
    global _FACTOR_RECOMMENDATIONS_SNAPSHOT
    global _FACTOR_SNAPSHOT_SCORER

    _FACTOR_ANALYSIS_SNAPSHOT = None
    _FACTOR_RECOMMENDATIONS_SNAPSHOT = None
    _FACTOR_SNAPSHOT_SCORER = None


def _ensure_factor_snapshots(request: Request) -> None:
    scorer = getattr(request.app.state, "scorer", None)
    if _FACTOR_ANALYSIS_SNAPSHOT is None or _FACTOR_SNAPSHOT_SCORER is not scorer:
        try:
            warm_factor_snapshots(scorer)
        except GraphUnavailableError as exc:
            raise HTTPException(status_code=503, detail="S2P graph unavailable for factor analysis") from exc


def _fallback_factor_stats() -> dict[str, dict[str, float]]:
    return {
        "match_status": {"variance": 0.85, "outcome_corr": 0.46},
        "amount_variance_ratio": {"variance": 0.75, "outcome_corr": 0.38},
        "duplicate_score": {"variance": 0.68, "outcome_corr": 0.34},
        "supplier_exception_history": {"variance": 0.52, "outcome_corr": 0.18},
        "payment_terms_impact": {"variance": 0.44, "outcome_corr": 0.12},
        "commodity_index_correlation": {"variance": 0.36, "outcome_corr": 0.08},
        "tax_regulatory_compliance": {"variance": 0.25, "outcome_corr": 0.03},
        "environmental_risk": {"variance": 0.25, "outcome_corr": 0.03},
    }


def _row_correct(row: dict[str, Any]) -> bool:
    value = row.get("is_correct", row.get("correct"))
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "correct"}
    return False


def _variance(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _correlation(values: list[float], outcomes: list[float]) -> float:
    if len(values) != len(outcomes) or len(values) < 2:
        return 0.0
    value_mean = sum(values) / len(values)
    outcome_mean = sum(outcomes) / len(outcomes)
    numerator = sum((value - value_mean) * (outcome - outcome_mean) for value, outcome in zip(values, outcomes))
    value_var = sum((value - value_mean) ** 2 for value in values)
    outcome_var = sum((outcome - outcome_mean) ** 2 for outcome in outcomes)
    denom = (value_var * outcome_var) ** 0.5
    return cast(float, numerator / denom) if denom else 0.0
