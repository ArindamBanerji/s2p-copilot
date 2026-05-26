"""S2P evidence endpoints for audit, lifecycle, and compliance views."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors
from app.routers.s2p_data_helpers import load_invoices
from app.services.receipt_store import get_receipt_store

router = APIRouter(prefix="/api/s2p/evidence", tags=["s2p-evidence"])

_load_invoices = load_invoices


def _invoice_by_id(invoice_id: str) -> dict[str, Any] | None:
    for invoice in _load_invoices():
        if invoice.get("invoice_id") == invoice_id or invoice.get("event_id") == invoice_id:
            return invoice
    return None


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


def _graph_domain(graph_store: Any | None = None) -> str:
    return str(getattr(graph_store, "domain", None) or "s2p")


def _conservation_snapshot(request: Request) -> dict[str, Any]:
    try:
        from app.routers.s2p import _current_conservation_status, _graph_verified_counts
    except Exception:
        return {}

    try:
        verified_count, correct_count = _graph_verified_counts(request)
        graph_store = _graph_store(request)
        domain = _graph_domain(graph_store)
        get_all_decisions = getattr(graph_store, "get_all_decisions", None)
        total_decisions = len(get_all_decisions(domain)) if callable(get_all_decisions) else verified_count
        return {
            "status": _current_conservation_status(request),
            "verified_count": verified_count,
            "correct_count": correct_count,
            "total_decisions": max(total_decisions, 0),
        }
    except Exception:
        return {}


def _override_distribution(receipts: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for receipt in receipts:
        if receipt.get("human_action") != receipt.get("scored_action"):
            reason = receipt.get("override_reason") or "unspecified"
            counter[str(reason)] += 1
    return dict(sorted(counter.items()))


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
                    for decision in graph_store.get_all_decisions(_graph_domain(graph_store))
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


@router.get("/receipts")
def receipts(limit: int = 50) -> dict[str, Any]:
    store = get_receipt_store()
    return {"receipts": store.get_chain(limit=limit), "stats": store.stats}


@router.get("/receipts/{invoice_id}")
def receipts_for_invoice(invoice_id: str) -> dict[str, Any]:
    store = get_receipt_store()
    invoice_receipts = store.get_for_invoice(invoice_id)
    if not invoice_receipts:
        raise HTTPException(status_code=404, detail=f"No receipts for invoice {invoice_id}")
    return {"invoice_id": invoice_id, "receipts": invoice_receipts}


@router.get("/chain-integrity")
def chain_integrity() -> dict[str, Any]:
    return get_receipt_store().verify_chain()


@router.get("/audit-pack")
def audit_pack(request: Request, limit: int = 100) -> dict[str, Any]:
    store = get_receipt_store()
    receipts_payload = store.get_chain(limit=limit)
    stats = store.stats
    return {
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "receipt_count": stats["total_receipts"],
        "chain_integrity": store.verify_chain(),
        "conservation_state": _conservation_snapshot(request),
        "override_distribution": _override_distribution(receipts_payload),
        "override_count": stats["overrides"],
        "confirm_count": stats["confirms"],
        "receipts": receipts_payload,
    }


def _line_item_quantity(invoice: dict[str, Any]) -> float | str:
    items = (invoice.get("metadata") or {}).get("line_items")
    if not isinstance(items, list):
        return "N/A"
    total = 0.0
    found = False
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            total += float(item.get("quantity", 0.0) or 0.0)
            found = True
        except (TypeError, ValueError):
            continue
    return round(total, 2) if found else "N/A"


def _template_variables(invoice: dict[str, Any]) -> dict[str, Any]:
    metadata = invoice.get("metadata") if isinstance(invoice.get("metadata"), dict) else {}
    factors = compute_all_factors(invoice)
    amount_variance = float(factors.get("amount_variance_ratio", 0.0) or 0.0)
    commodity_delta = float(factors.get("commodity_index_correlation", 0.0) or 0.0)
    duplicate_score = float(factors.get("duplicate_score", 0.0) or 0.0)
    match_score = float(factors.get("match_status", 0.0) or 0.0)
    tax_score = float(factors.get("tax_regulatory_compliance", 0.0) or 0.0)
    supplier = invoice.get("supplier_name") or invoice.get("supplier_id") or "N/A"
    invoice_id = invoice.get("invoice_id") or invoice.get("event_id") or "N/A"
    action = invoice.get("ground_truth_action") or "hold_for_review"
    quantity = _line_item_quantity(invoice)
    po_quantity = round(float(quantity) * 0.96, 2) if isinstance(quantity, (int, float)) else "N/A"
    delta = round(float(quantity) - po_quantity, 2) if isinstance(quantity, (int, float)) else "N/A"
    compliance_pct = round(max(0.0, min(1.0, 1.0 - tax_score)) * 100.0, 1)
    return {
        "invoice_id": invoice_id,
        "supplier": supplier,
        "variance_pct": round(amount_variance * 100.0, 1),
        "commodity": metadata.get("commodity") or "N/A",
        "commodity_delta": round(commodity_delta * 100.0, 1),
        "lookback": 30,
        "ref": metadata.get("contract_ref") or invoice.get("contract_id") or "N/A",
        "allows_blocks": "allows" if amount_variance <= 0.2 else "blocks",
        "threshold": 20,
        "within_exceeds": "within" if amount_variance <= 0.2 else "exceeds",
        "action": action,
        "score": round(max(factors.values()) if factors else 0.0, 3),
        "inv_qty": quantity,
        "po_qty": po_quantity,
        "delta": delta,
        "gr_qty": po_quantity,
        "match_status": "matched" if match_score >= 0.7 else "mismatch requires review",
        "match_id": f"{invoice_id}-PRIOR",
        "match_date": metadata.get("invoice_date") or "N/A",
        "match_amt": invoice.get("amount", "N/A"),
        "similarity": round(duplicate_score * 100.0, 1),
        "verdict": "possible duplicate" if duplicate_score >= 0.5 else "no duplicate pattern",
        "po_id": invoice.get("po_number") or "N/A",
        "scope": metadata.get("commodity") or invoice.get("category") or "N/A",
        "covered_pct": round(match_score * 100.0, 1),
        "gap_items": "line-item coverage" if match_score < 0.8 else "none",
        "n_rules": 1 if tax_score >= 0.3 else 0,
        "issues": "tax/regulatory completeness" if tax_score >= 0.3 else "none",
        "compliance_pct": compliance_pct,
    }


@router.get("/template")
def evidence_template(category: str, invoice_id: str) -> dict[str, Any]:
    if category not in S2PDomainConfig.evidence_templates:
        raise HTTPException(status_code=400, detail=f"Unknown category: {category}")
    invoice = _invoice_by_id(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    variables = _template_variables(invoice)
    template = S2PDomainConfig.evidence_templates[category]
    rendered = template.format_map(defaultdict(lambda: "N/A", variables))
    return {
        "invoice_id": invoice.get("invoice_id") or invoice_id,
        "category": category,
        "template": template,
        "rendered": rendered,
        "variables": variables,
    }


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
