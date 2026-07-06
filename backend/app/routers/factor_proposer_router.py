"""S2P factor proposer endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.domains.s2p.config import S2PDomainConfig
from app.models.responses import GenericResponse
from app.routers.s2p_explorer import _decision_factor_vector, _graph_domain, _read_dk_weights
from app.services.factor_proposer import FactorProposer


router = APIRouter(prefix="/api/s2p/factors", tags=["s2p-factor-proposer"])


class FactorProposalRequest(BaseModel):
    factor: str
    candidates: list[str] = []


@router.get("/analysis", response_model=GenericResponse)
def factor_analysis(request: Request) -> dict[str, Any]:
    recommendations = _proposer(request).analyze()
    return {
        "factors": [item.to_dict() for item in recommendations],
        "count": len(recommendations),
        "advisory": True,
    }


@router.get("/recommendations", response_model=GenericResponse)
def factor_recommendations(request: Request) -> dict[str, Any]:
    recommendations = [
        item.to_dict()
        for item in _proposer(request).analyze()
        if item.verdict == "replace_candidate"
    ]
    return {
        "recommendations": recommendations,
        "count": len(recommendations),
        "advisory": True,
    }


@router.post("/propose", response_model=GenericResponse)
def propose_factor(payload: FactorProposalRequest, request: Request) -> dict[str, Any]:
    try:
        return _proposer(request).propose_replacement(payload.factor, payload.candidates)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _proposer(request: Request) -> FactorProposer:
    factors = list(S2PDomainConfig.factors)
    scorer = getattr(request.app.state, "scorer", None)
    weights = _dk_weight_map(scorer, factors)
    return FactorProposer(weights, _factor_stats(request, factors))


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


def _factor_stats(request: Request, factors: list[str]) -> dict[str, dict[str, float]]:
    live = _live_factor_stats(getattr(request.app.state, "scorer", None), factors)
    defaults = _fallback_factor_stats()
    if not live:
        return {factor: defaults.get(factor, {"variance": 0.30, "outcome_corr": 0.05}) for factor in factors}
    return {factor: live.get(factor, defaults.get(factor, {"variance": 0.30, "outcome_corr": 0.05})) for factor in factors}


def _live_factor_stats(scorer: Any, factors: list[str]) -> dict[str, dict[str, float]]:
    graph_store = getattr(scorer, "graph_store", None)
    get_all_decisions = getattr(graph_store, "get_all_decisions", None)
    if not callable(get_all_decisions):
        return {}
    rows = [row for row in get_all_decisions(_graph_domain(graph_store)) if isinstance(row, dict)]
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
    return numerator / denom if denom else 0.0
    return {factor: defaults.get(factor, {"variance": 0.30, "outcome_corr": 0.05}) for factor in factors}
