"""
S2P Copilot router — domain-specific endpoints.
Framework endpoints are in framework_router.py (copied from SOC).
This file: S2P procurement domain endpoints only.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import S2PEvent, compute_all_factors
from app.domains.s2p.scorer import score_event, update_scorer

router = APIRouter(prefix="/api/s2p", tags=["S2P"])

DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def _load_synthetic_invoices() -> list[dict]:
    path = DATA_DIR / "synthetic_invoices.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data if isinstance(data, list) else []


def _find_invoice(event_id_or_invoice_id: str) -> dict | None:
    for invoice in _load_synthetic_invoices():
        if not isinstance(invoice, dict):
            continue
        if invoice.get("invoice_id") == event_id_or_invoice_id:
            return invoice
        if invoice.get("event_id") == event_id_or_invoice_id:
            return invoice
    return None


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
        return {"neighbors": context_raw}
    if isinstance(context_raw, dict):
        return context_raw
    return None


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
        name: getattr(request, name)
        for name in S2PDomainConfig.factors
        if getattr(request, name) is not None
    }
    if explicit_factors:
        factors = dict(invoice.get("factors") or {})
        factors.update(explicit_factors)
        invoice["factors"] = factors
        for name, value in explicit_factors.items():
            invoice[name] = value
    return invoice


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
    match_status: Optional[float] = None
    amount_variance_ratio: Optional[float] = None
    duplicate_score: Optional[float] = None
    supplier_exception_history: Optional[float] = None
    payment_terms_impact: Optional[float] = None
    commodity_index_correlation: Optional[float] = None
    tax_regulatory_compliance: Optional[float] = None


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
def score_procurement_event(request: ScoreRequest, http_request: Request) -> ScoreResponse:
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

    fixture_invoice = _find_invoice(request.event_id)
    invoice = _invoice_from_request(request, fixture_invoice)
    lookup_id = invoice.get("invoice_id") or request.event_id
    context = _resolve_graph_context(lookup_id, http_request)
    computed_factors = compute_all_factors(invoice, context=context)
    factor_vector = [computed_factors[name] for name in S2PDomainConfig.factors]
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


class OutcomeRequest(BaseModel):
    decision_id: str
    outcome: str            # "confirm" or "override"
    analyst_action: str     # action analyst chose
    analyst_id: str
    factor_vector: list[float]  # original factor vector
    category: str
    predicted_action: str       # original model prediction


class OutcomeResponse(BaseModel):
    decision_id: str
    outcome: str
    learning_applied: bool


@router.post("/outcome")
def record_outcome(request: OutcomeRequest) -> OutcomeResponse:
    """
    Record analyst outcome and optionally update centroids.
    POST /api/s2p/outcome
    """
    if request.outcome not in ("confirm", "override"):
        raise HTTPException(status_code=422,
            detail="outcome must be 'confirm' or 'override'")

    if request.analyst_action not in S2PDomainConfig.actions:
        raise HTTPException(status_code=422,
            detail=f"analyst_action must be one of {S2PDomainConfig.actions}")

    if len(request.factor_vector) != S2PDomainConfig.n_factors:
        raise HTTPException(status_code=422,
            detail=f"factor_vector must contain {S2PDomainConfig.n_factors} values")

    # Write to Neo4j (fault-tolerant)
    try:
        from app.db.neo4j import neo4j_client
        from app.domains.s2p.graph import write_s2p_outcome
        write_s2p_outcome(neo4j_client, request.decision_id,
            request.outcome, request.analyst_action, request.analyst_id)
    except Exception:
        pass  # Neo4j unavailable — outcome still processed

    # Update centroids
    learning_applied = update_scorer(
        request.factor_vector, request.category,
        request.predicted_action, request.analyst_action,
    )

    return OutcomeResponse(
        decision_id=request.decision_id,
        outcome=request.outcome,
        learning_applied=learning_applied,
    )


@router.get("/iks")
def get_iks() -> dict:
    """
    GET /api/s2p/iks
    Returns current S2P Institutional Knowledge Score.
    """
    from app.domains.s2p.scorer import get_s2p_iks
    result = get_s2p_iks()

    # Optionally enrich with Neo4j decision count
    try:
        from app.db.neo4j import neo4j_client
        with neo4j_client.session() as session:
            r = session.run("MATCH (d:S2PDecision) RETURN count(d) AS n")
            result["decisions"] = r.single()["n"]
    except Exception:
        pass  # Neo4j unavailable — use placeholder 0

    return result


@router.get("/learning-gate")
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

    # Read decision counts from Neo4j (fault-tolerant)
    try:
        from app.db.neo4j import neo4j_client
        with neo4j_client.session() as session:
            # Verified decisions: S2PDecision nodes with outcome set
            r = session.run(
                "MATCH (d:S2PDecision) WHERE d.outcome IS NOT NULL "
                "RETURN count(d) AS verified"
            )
            verified_decisions = int(r.single()["verified"] or 0)

            # Override precision: correct overrides / total overrides
            r2 = session.run(
                "MATCH (d:S2PDecision) WHERE d.outcome = 'override' "
                "RETURN count(d) AS total_overrides, "
                "sum(CASE WHEN d.action = d.analyst_action THEN 1 ELSE 0 END) "
                "AS correct_overrides"
            )
            rec = r2.single()
            total_ov = int(rec["total_overrides"] or 0)
            correct_ov = int(rec["correct_overrides"] or 0)
            if total_ov > 0:
                override_precision = correct_ov / total_ov
    except Exception:
        pass  # Neo4j unavailable — fall back to cold-start defaults

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
