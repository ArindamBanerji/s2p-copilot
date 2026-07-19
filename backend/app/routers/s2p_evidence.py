"""S2P evidence endpoints for audit, lifecycle, and compliance views."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from copilot_sdk.state.cached_static import cached_static

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors
from app.models.responses import GenericResponse
from app.routers.s2p_data_helpers import load_invoices
from app.services.receipt_store import get_receipt_store
from app.services.s2p_evidence_templates import S2PEvidenceEngine, evidence_context_from_record
from app.services.s2p_situation_pattern import S2PInvoiceTraversalPattern
from app.services.s2p_trust_explanations import format_trust_explanation
from copilot_sdk.situation import SituationAnalyzer

router = APIRouter(prefix="/api/s2p/evidence", tags=["s2p-evidence"])

_load_invoices = load_invoices
_evidence_engine = S2PEvidenceEngine()


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


def _scorer(request: Request) -> Any | None:
    state = getattr(request.app, "state", None)
    return getattr(state, "scorer", None)


def _graph_domain(graph_store: Any | None = None) -> str:
    return str(getattr(graph_store, "domain", None) or "s2p")


def _trust_explanation_payload(
    request: Request,
    *,
    category: str,
    action: str,
    confidence: float,
    variables: dict[str, Any],
) -> dict[str, Any]:
    scorer = _scorer(request)
    category_index = _category_index(category)
    weights = _call_or_none(getattr(scorer, "get_dk_weights", None))
    phase = _call_or_none(getattr(scorer, "get_category_phase", None), category)
    verified_count = _call_or_none(getattr(scorer, "get_verified_count", None))
    centroid = _call_or_none(getattr(scorer, "get_centroid", None), category, action)
    if centroid is None:
        centroid = (
            S2PDomainConfig.get_initial_centroids()
            .get(category, {})
            .get(action)
        )
    explanation = format_trust_explanation(
        category=category,
        recommended_action=action,
        confidence=confidence,
        factor_values=variables.get("factors") if isinstance(variables.get("factors"), dict) else {},
        factor_names=list(S2PDomainConfig.factors),
        dk_weights=weights,
        centroid=centroid,
        category_index=category_index,
        phase=str(phase) if phase else None,
        verified_count=verified_count if isinstance(verified_count, int) else None,
        verified_target=200,
    )
    payload = explanation.to_dict()
    factor_value_provenance = _provenance(
        "context",
        "factor value · scorer input context",
    )
    dk_weight_provenance = (
        _provenance("scorer", "DK trust weight · learned from verified outcomes")
        if payload.get("trust_available")
        else _provenance("unavailable", "DK trust weight · learning")
    )
    for factor in payload.get("factors", []):
        if isinstance(factor, dict):
            factor.update(factor_value_provenance)
            factor["factor_value_provenance"] = dict(factor_value_provenance)
            factor["dk_weight_provenance"] = dict(dk_weight_provenance)
    payload["provenance"] = factor_value_provenance
    payload["dk_weight_provenance"] = dk_weight_provenance
    return payload


def _category_index(category: str) -> int | None:
    try:
        return S2PDomainConfig.get_category_index(category)
    except (ValueError, AttributeError):
        return None


def _call_or_none(fn: Any, *args: Any) -> Any | None:
    if not callable(fn):
        return None
    try:
        return fn(*args)
    except Exception:
        return None


def _provenance(source: str, label: str) -> dict[str, Any]:
    if source == "scorer":
        return {
            "source": "scorer",
            "provenance_label": label,
            "provenance_tier": "learned",
            "integration_status": "configured",
            "measured": True,
            "display_prefix": "██ learned",
        }
    if source == "unavailable":
        return {
            "source": "unavailable",
            "provenance_label": label,
            "provenance_tier": "unavailable",
            "integration_status": "pending",
            "measured": False,
            "display_prefix": "integration pending",
        }
    return {
        "source": source,
        "provenance_label": label,
        "provenance_tier": "context",
        "integration_status": "pending",
        "measured": False,
        "display_prefix": "░░ context",
    }


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


@router.get("/audit-trail/{invoice_id}", response_model=GenericResponse)
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


@router.get("/receipts", response_model=GenericResponse)
def receipts(limit: int = 50) -> dict[str, Any]:
    store = get_receipt_store()
    return {"receipts": store.get_chain(limit=limit), "stats": store.stats}


@router.get("/receipts/{invoice_id}", response_model=GenericResponse)
def receipts_for_invoice(invoice_id: str) -> dict[str, Any]:
    store = get_receipt_store()
    invoice_receipts = store.get_for_invoice(invoice_id)
    if not invoice_receipts:
        raise HTTPException(status_code=404, detail=f"No receipts for invoice {invoice_id}")
    return {"invoice_id": invoice_id, "receipts": invoice_receipts}


@router.get("/receipts/decision/{decision_id}", response_model=GenericResponse)
def receipts_for_decision(decision_id: str) -> dict[str, Any]:
    store = get_receipt_store()
    decision_receipts = store.get_for_decision(decision_id)
    if not decision_receipts:
        raise HTTPException(status_code=404, detail=f"No receipts for decision {decision_id}")
    return {"decision_id": decision_id, "receipts": decision_receipts}


@router.get("/chain-integrity", response_model=GenericResponse)
@cached_static("evidence-chain-integrity", copilot="s2p")
def chain_integrity() -> dict[str, Any]:
    return get_receipt_store().verify_chain()


@router.get("/audit-pack", response_model=GenericResponse)
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


@router.get("/template", response_model=GenericResponse)
def evidence_template(
    request: Request,
    category: str,
    invoice_id: str | None = None,
) -> dict[str, Any]:
    invoice = _invoice_by_id(invoice_id) if invoice_id else None
    invoice_found = invoice is not None
    if invoice is None:
        invoice = {
            "category": category,
            "factors": {},
        }
        if invoice_id:
            invoice.update({"invoice_id": invoice_id, "event_id": invoice_id})
    graph_store = _graph_store(request)
    analyzer = SituationAnalyzer(
        [S2PInvoiceTraversalPattern(scorer=_scorer(request))],
        default_max_depth=3,
        max_allowed_depth=5,
    )
    scope = {"category": category}
    if invoice_id:
        scope["invoice_id"] = invoice_id
    intent = analyzer.normalize_signal(
        {
            "domain": "s2p",
            "intent_type": "evidence_template",
            "verb": "explain",
            "subject": "invoice",
            "source_event_id": invoice_id,
            "scope": scope,
            "payload": invoice,
        }
    )
    situation_context = analyzer.analyze_intent(intent, graph_store=graph_store, max_depth=3)
    variables = evidence_context_from_record(invoice)
    context_used = situation_context.metadata.get("context_used")
    if isinstance(context_used, dict):
        variables.update({key: value for key, value in context_used.items() if value is not None})
    action = (
        str(variables.get("action") or invoice.get("ground_truth_action") or "unknown")
    )
    confidence = float(variables.get("score") or 0.0)
    evidence = _evidence_engine.render(category, variables, action, confidence)
    evidence_payload = evidence.to_dict()
    trust_explanation = _trust_explanation_payload(
        request,
        category=category,
        action=action,
        confidence=confidence,
        variables=variables,
    )
    evidence_payload["trust_explanation"] = trust_explanation
    evidence_payload["trust_weighted_factors"] = trust_explanation["factors"]
    evidence_payload["trust_available"] = trust_explanation["trust_available"]
    evidence_payload["trust_learning_message"] = trust_explanation["learning_message"]
    if not invoice_id and "invoice_id" not in evidence_payload["missing_fields"]:
        evidence_payload["missing_fields"].append("invoice_id")
        evidence_payload["missing_fields"].sort()
    template = evidence.template or ""
    rendered = evidence.text
    return {
        "invoice_id": invoice.get("invoice_id") or invoice_id or "unknown",
        "invoice_found": invoice_found,
        "category": category,
        "template": template,
        "rendered": rendered,
        "variables": variables,
        "evidence": evidence_payload,
        "trust_explanation": trust_explanation,
        "trust_weighted_factors": trust_explanation["factors"],
        "trust_available": trust_explanation["trust_available"],
        "trust_learning_message": trust_explanation["learning_message"],
        "situation_context": situation_context.to_dict(),
    }


@router.get("/rules", response_model=GenericResponse)
@cached_static("evidence-rules", copilot="s2p")
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


@router.get("/compliance", response_model=GenericResponse)
@cached_static("evidence-compliance", copilot="s2p")
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
