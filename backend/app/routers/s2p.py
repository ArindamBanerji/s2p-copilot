"""
S2P Copilot router — domain-specific endpoints.
Framework endpoints are in framework_router.py (copied from SOC).
This file: S2P procurement domain endpoints only.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import S2PEvent, compute_factor_vector
from app.domains.s2p.scorer import score_event

router = APIRouter(prefix="/api/s2p", tags=["S2P"])


class ScoreRequest(BaseModel):
    event_id: str
    category: str
    amount: float
    supplier_id: str
    contract_id: Optional[str] = None
    approved_categories: Optional[list[str]] = None
    supplier_risk_rating: float = 0.5
    historical_spend_mean: float = 0.0
    historical_spend_std: float = 1.0
    vendor_decisions: int = 0
    vendor_approvals: int = 0


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


@router.post("/score")
def score_procurement_event(request: ScoreRequest) -> ScoreResponse:
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
    )

    factor_vector = compute_factor_vector(event)
    result = score_event(factor_vector, request.category)

    try:
        from app.db.neo4j import neo4j_client
        from app.domains.s2p.graph import write_s2p_decision
        decision_id = write_s2p_decision(
            neo4j_client,
            event_id=request.event_id,
            category=request.category,
            action=result["action"],
            action_index=result["action_index"],
            confidence=result["confidence"],
            factor_vector=factor_vector,
            factor_names=S2PDomainConfig.factors,
            supplier_id=request.supplier_id,
            amount=request.amount,
        )
    except Exception:
        decision_id = f"S2P-{request.event_id}-local"

    return ScoreResponse(
        event_id=request.event_id,
        category=request.category,
        action=result["action"],
        action_index=result["action_index"],
        confidence=result["confidence"],
        probabilities=result["probabilities"],
        factor_vector=factor_vector,
        factor_names=S2PDomainConfig.factors,
        decision_id=decision_id,
    )
