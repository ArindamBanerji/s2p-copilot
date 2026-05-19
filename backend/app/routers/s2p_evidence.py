"""S2P evidence endpoints for audit, lifecycle, and compliance views."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors
from app.routers.s2p_data_helpers import load_invoices

router = APIRouter(prefix="/api/s2p/evidence", tags=["s2p-evidence"])

_load_invoices = load_invoices


def _graph_store(request: Request) -> Any | None:
    try:
        graph_store = request.app.state.graph_store
    except AttributeError:
        graph_store = None
    if graph_store is not None:
        return graph_store
    state = getattr(request.app, "state", None)
    scorer = getattr(state, "scorer", None)
    return getattr(scorer, "graph_store", None)


def _decision_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata")
    return dict(metadata) if isinstance(metadata, dict) else {}


def _decision_matches_invoice(
    decision: dict[str, Any],
    invoice_id: str,
) -> bool:
    metadata = _decision_metadata(decision)
    return (
        decision.get("entity_id") == invoice_id
        or decision.get("decision_id") == invoice_id
        or decision.get("invoice_id") == invoice_id
        or decision.get("source_invoice_id") == invoice_id
        or metadata.get("invoice_id") == invoice_id
        or metadata.get("entity_id") == invoice_id
        or metadata.get("source_invoice_id") == invoice_id
    )


def _enrich_decision_invoice_metadata(decision: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(decision)
    metadata = dict(enriched.get("metadata") or {})
    enriched["metadata"] = metadata
    for key in ("invoice_id", "source_invoice_id", "supplier_id", "supplier_name", "amount"):
        value = metadata.get(key) or enriched.get(key)
        if value is not None:
            enriched[key] = value
    return enriched


def _graph_linked_decisions(graph_store: Any, invoice_id: str) -> list[dict[str, Any]]:
    get_decision_links = getattr(graph_store, "get_decision_links", None)
    get_decision = getattr(graph_store, "get_decision", None)
    if not callable(get_decision_links) or not callable(get_decision):
        return []
    try:
        links = get_decision_links()
    except Exception:
        return []

    linked_decision_ids: list[str] = []
    seen: set[str] = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("edge_type") != "DECIDED_ON" or link.get("entity_id") != invoice_id:
            continue
        decision_id = link.get("decision_id")
        if isinstance(decision_id, str) and decision_id not in seen:
            seen.add(decision_id)
            linked_decision_ids.append(decision_id)

    decisions: list[dict[str, Any]] = []
    for decision_id in linked_decision_ids:
        try:
            decision = get_decision(decision_id)
        except Exception:
            continue
        if isinstance(decision, dict):
            decisions.append(_enrich_decision_invoice_metadata(decision))
    return decisions


@router.get("/audit-trail/{invoice_id}")
def audit_trail(invoice_id: str, request: Request) -> dict[str, Any]:
    graph_store = _graph_store(request)
    decisions: list[dict[str, Any]] = []
    if graph_store is not None:
        decisions = _graph_linked_decisions(graph_store, invoice_id)
    if graph_store is not None and hasattr(graph_store, "get_all_decisions"):
        try:
            if not decisions:
                decisions = [
                    _enrich_decision_invoice_metadata(decision)
                    for decision in graph_store.get_all_decisions()
                    if isinstance(decision, dict) and _decision_matches_invoice(decision, invoice_id)
                ]
        except Exception:
            decisions = []
    if not decisions and graph_store is not None and hasattr(graph_store, "get_decision"):
        try:
            decision = graph_store.get_decision(invoice_id)
        except Exception:
            decision = None
        if isinstance(decision, dict):
            decisions = [_enrich_decision_invoice_metadata(decision)]
    return {"invoice_id": invoice_id, "decisions": decisions, "count": len(decisions)}


@router.get("/rules")
def rules() -> dict[str, Any]:
    ruleset = [
        {
            "rule_id": "S2P-RULE-EXCEPTION-CLUSTER",
            "name": "Supplier exception cluster threshold",
            "state": "promoted",
            "action": "hold_for_review",
            "factor": "supplier_exception_history",
        },
        {
            "rule_id": "S2P-RULE-COMMODITY-DRIFT",
            "name": "Commodity volatility drift review",
            "state": "shadow",
            "action": "flag_leakage",
            "factor": "commodity_index_correlation",
        },
        {
            "rule_id": "S2P-RULE-TAX-CHECK",
            "name": "Tax and withholding completeness",
            "state": "proposed",
            "action": "refer_to_specialist",
            "factor": "tax_regulatory_compliance",
        },
        {
            "rule_id": "S2P-RULE-LOW-CONFIDENCE-AUTO",
            "name": "Reject low-confidence auto approval",
            "state": "rejected",
            "action": "auto_approve",
            "factor": "match_status",
        },
    ]
    return {
        "rules": ruleset,
        "count": len(ruleset),
        "source": "fixture",
        "note": "Based on seeded evolution data.",
    }


@router.get("/compliance")
def compliance() -> dict[str, Any]:
    invoices = _load_invoices()
    flagged: list[dict[str, Any]] = []
    compliant = 0
    for invoice in invoices:
        factors = compute_all_factors(invoice)
        tax_score = float(factors.get("tax_regulatory_compliance", 1.0))
        is_compliant = tax_score < 0.3
        compliant += int(is_compliant)
        if not is_compliant:
            flagged.append(
                {
                    "invoice_id": invoice.get("invoice_id"),
                    "category": invoice.get("category"),
                    "supplier_id": invoice.get("supplier_id"),
                    "tax_regulatory_compliance": tax_score,
                    "recommended_action": invoice.get("ground_truth_action"),
                }
            )
    total = len(invoices)
    return {
        "total": total,
        "compliant": compliant,
        "compliant_pct": round(compliant / total, 4) if total else 0.0,
        "flagged_count": len(flagged),
        "flagged_invoices": flagged[:20],
        "factor": S2PDomainConfig.factors[-1],
    }
