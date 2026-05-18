"""
S2P Copilot router — domain-specific endpoints.
Framework endpoints are in framework_router.py (copied from SOC).
This file: S2P procurement domain endpoints only.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Any, Optional

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import S2PEvent, compute_all_factors
from app.routers.s2p_data_helpers import find_invoice
from app.routers.s2p_preview import _load_celonis_cache

router = APIRouter(prefix="/api/s2p", tags=["S2P"])
learn_router = APIRouter(prefix="/api", tags=["S2P"])

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


def _score_process_context() -> dict | None:
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


def _sdk_scorer(http_request: Request):
    state = getattr(http_request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    if scorer is None:
        raise HTTPException(status_code=500, detail="S2P scorer is not configured")
    return scorer


def _invoice_decision_metadata(invoice: dict[str, Any]) -> dict[str, Any]:
    invoice_id = str(invoice.get("invoice_id") or invoice.get("event_id") or "")
    metadata = {
        "invoice_id": invoice_id,
        "source_invoice_id": invoice_id,
        "supplier_id": invoice.get("supplier_id"),
        "supplier_name": invoice.get("supplier_name"),
        "amount": invoice.get("amount"),
    }
    return {key: value for key, value in metadata.items() if value not in (None, "")}


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
    try:
        result = scorer.learn(
            decision_id,
            actual_action,
            outcome,
            context=_decision_context(decision, context),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown decision: {decision_id}") from exc
    payload = _json_safe(result)
    if isinstance(payload, dict) and isinstance(decision, dict):
        invoice_id = _decision_invoice_id(decision)
        if invoice_id:
            payload.setdefault("invoice_id", invoice_id)
    return payload


def _ensure_outcome_decision(scorer: Any, request: "OutcomeRequest") -> None:
    if scorer.graph_store.get_decision(request.decision_id) is not None:
        return
    factors = {
        name: float(request.factor_vector[index])
        for index, name in enumerate(S2PDomainConfig.factors)
    }
    category_index = S2PDomainConfig.get_category_index(request.category)
    recommended_index = S2PDomainConfig.get_action_index(request.predicted_action)
    scorer.graph_store.write_decision(
        entity_id=request.decision_id,
        category=request.category,
        action=request.predicted_action,
        confidence=1.0,
        factors=factors,
        metadata={
            "decision_id": request.decision_id,
            "domain": "s2p",
            "category_index": category_index,
            "factor_vector": list(request.factor_vector),
            "recommended_index": recommended_index,
            "probabilities": [
                1.0 if index == recommended_index else 0.0
                for index in range(S2PDomainConfig.n_actions)
            ],
        },
    )


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
    process_context: Optional[dict] = None


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

    fixture_invoice = find_invoice(request.event_id)
    invoice = _invoice_from_request(request, fixture_invoice)
    lookup_id = invoice.get("invoice_id") or request.event_id
    context = _resolve_graph_context(lookup_id, http_request)
    computed_factors = compute_all_factors(invoice, context=context)
    factor_vector = [computed_factors[name] for name in S2PDomainConfig.factors]
    scorer = _sdk_scorer(http_request)
    try:
        score_result = scorer.score(
            computed_factors,
            request.category,
            metadata=_invoice_decision_metadata(invoice),
        )
    except AssertionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        from app.db.neo4j import neo4j_client
        from app.domains.s2p.graph import write_s2p_decision
        write_s2p_decision(
            neo4j_client,
            event_id=request.event_id,
            category=request.category,
            action=score_result.action,
            action_index=score_result.action_index,
            confidence=score_result.confidence,
            factor_vector=factor_vector,
            factor_names=S2PDomainConfig.factors,
            supplier_id=request.supplier_id,
            amount=request.amount,
        )
    except Exception:
        pass

    return ScoreResponse(
        event_id=request.event_id,
        category=request.category,
        action=score_result.action,
        action_index=score_result.action_index,
        confidence=score_result.confidence,
        probabilities=score_result.probabilities,
        factor_vector=factor_vector,
        factor_names=S2PDomainConfig.factors,
        decision_id=score_result.decision_id,
        process_context=_score_process_context(),
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


@learn_router.post("/learn")
def learn_decision(request: LearnRequest, http_request: Request) -> dict[str, Any]:
    """SDK-shaped learn endpoint backed by the S2P CompoundingScorer."""
    if request.actual_action not in S2PDomainConfig.actions:
        raise HTTPException(
            status_code=422,
            detail=f"actual_action must be one of {S2PDomainConfig.actions}",
        )
    return _learn_with_scorer(
        _sdk_scorer(http_request),
        request.decision_id,
        request.actual_action,
        request.outcome,
        request.context or {},
    )


@router.post("/outcome")
def record_outcome(request: OutcomeRequest, http_request: Request) -> dict[str, Any]:
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

    try:
        from app.db.neo4j import neo4j_client
        from app.domains.s2p.graph import write_s2p_outcome
        write_s2p_outcome(neo4j_client, request.decision_id,
            request.outcome, request.analyst_action, request.analyst_id)
    except Exception:
        pass  # Neo4j unavailable — outcome still processed

    scorer = _sdk_scorer(http_request)
    _ensure_outcome_decision(scorer, request)
    try:
        decision = scorer.graph_store.get_decision(request.decision_id)
    except Exception:
        decision = None
    invoice_id = _decision_invoice_id(decision)
    payload = _learn_with_scorer(
        scorer,
        request.decision_id,
        request.analyst_action,
        request.outcome,
        {
            "amount": request.amount,
            "at_risk": request.at_risk,
            "recovery_pct": request.recovery_pct,
            "invoice_id": invoice_id or None,
        },
    )
    payload["outcome"] = request.outcome
    payload["learning_applied"] = payload.get("status") != "paused"
    return payload


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
