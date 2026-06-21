"""S2P Control Tower endpoints for intent classification and queue priority."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors
from app.models.intents import INTENT_METADATA, IntentCategory, IntentType
from app.models.responses import CollectionResponse, GenericResponse
from app.routers.s2p_data_helpers import find_invoice, load_invoices
from app.services.intent_classifier import classify_intent

router = APIRouter(prefix="/api/s2p/control-tower", tags=["s2p-control-tower"])


CATEGORY_INTENT: dict[str, IntentType] = {
    "price_variance": IntentType.triage_price,
    "quantity_mismatch": IntentType.triage_quantity,
    "duplicate_risk": IntentType.triage_duplicate,
    "contract_gap": IntentType.triage_contract,
    "format_compliance": IntentType.triage_format,
}

INTENT_CATEGORIES: dict[IntentType, list[str]] = {
    IntentType.triage_price: ["price_variance"],
    IntentType.triage_quantity: ["quantity_mismatch"],
    IntentType.triage_duplicate: ["duplicate_risk"],
    IntentType.triage_contract: ["contract_gap"],
    IntentType.triage_format: ["format_compliance"],
    IntentType.auto_approve: list(S2PDomainConfig.categories),
    IntentType.hold_review: list(S2PDomainConfig.categories),
    IntentType.escalate_buyer: ["price_variance", "quantity_mismatch", "contract_gap"],
    IntentType.escalate_manager: ["contract_gap", "format_compliance"],
    IntentType.refer_specialist: ["contract_gap", "format_compliance"],
    IntentType.query_invoice: list(S2PDomainConfig.categories),
    IntentType.query_supplier: list(S2PDomainConfig.categories),
    IntentType.query_compliance: ["contract_gap", "format_compliance"],
    IntentType.query_conservation: list(S2PDomainConfig.categories),
    IntentType.report_financial: ["price_variance", "duplicate_risk", "contract_gap"],
    IntentType.report_audit: ["duplicate_risk", "contract_gap", "format_compliance"],
    IntentType.batch_process: list(S2PDomainConfig.categories),
}

INTENT_ROUTES: dict[IntentCategory, str] = {
    IntentCategory.triage: "triage",
    IntentCategory.action: "action",
    IntentCategory.query: "query",
    IntentCategory.operational: "report",
}

INTENT_PANELS: dict[IntentType, list[str]] = {
    IntentType.triage_price: ["factor_fingerprint", "similar_invoices", "pvg_leakage"],
    IntentType.triage_quantity: ["process_context", "factor_fingerprint", "audit_trail"],
    IntentType.triage_duplicate: ["similar_invoices", "audit_trail", "supplier_heatmap"],
    IntentType.triage_contract: ["rule_lifecycle", "compliance", "audit_trail"],
    IntentType.triage_format: ["factor_fingerprint", "process_context"],
    IntentType.auto_approve: ["process_context", "audit_trail"],
    IntentType.hold_review: ["process_context", "factor_fingerprint"],
    IntentType.escalate_buyer: ["process_context", "supplier_heatmap", "audit_trail"],
    IntentType.escalate_manager: ["compliance", "audit_trail"],
    IntentType.refer_specialist: ["compliance", "rule_lifecycle"],
    IntentType.query_invoice: ["process_context", "audit_trail"],
    IntentType.query_supplier: ["supplier_heatmap", "similar_invoices"],
    IntentType.query_compliance: ["compliance", "rule_lifecycle"],
    IntentType.query_conservation: ["factor_fingerprint", "process_context"],
    IntentType.report_financial: ["pvg_leakage", "factor_fingerprint"],
    IntentType.report_audit: ["audit_trail", "compliance"],
    IntentType.batch_process: ["process_context", "factor_fingerprint"],
}


def _intent_payload(intent_id: str) -> dict[str, Any]:
    intent_type = IntentType(intent_id)
    metadata = INTENT_METADATA[intent_type]
    category = metadata["category"]
    return {
        "intent_id": intent_type.value,
        "name": intent_type.value,
        "category": category.value,
        "description": metadata["description"],
        "categories": INTENT_CATEGORIES[intent_type],
        "route": INTENT_ROUTES[category],
        "evidence_panels": INTENT_PANELS[intent_type],
        "default_action": metadata.get("default_action"),
        "priority": metadata.get("priority"),
    }


def _infer_intent(category: str, factors: dict[str, float]) -> tuple[str, str]:
    if category in CATEGORY_INTENT:
        return CATEGORY_INTENT[category].value, "category_mapping"
    if float(factors.get("duplicate_score", 0.0)) > 0.7:
        return IntentType.triage_duplicate.value, "factor_inference"
    if float(factors.get("amount_variance_ratio", 0.0)) > 0.3:
        return IntentType.triage_price.value, "factor_inference"
    if float(factors.get("match_status", 0.0)) > 0.65:
        return IntentType.triage_quantity.value, "factor_inference"
    if (
        float(factors.get("tax_regulatory_compliance", 0.0)) > 0.65
        or float(factors.get("payment_terms_impact", 0.0)) > 0.7
    ):
        return IntentType.triage_contract.value, "factor_inference"
    return IntentType.triage_format.value, "fallback"


def _classify_invoice(invoice: dict[str, Any], category_override: str | None = None) -> dict[str, Any]:
    category = category_override or str(invoice.get("category") or "")
    if category not in S2PDomainConfig.categories:
        raise HTTPException(status_code=422, detail=f"Unknown S2P category: {category}")
    factors = compute_all_factors({**invoice, "category": category})
    classified_intent = classify_intent({**invoice, "category": category})
    intent_id = classified_intent.intent.value
    source = "intent_classifier"
    intent = _intent_payload(intent_id)
    amount = float(invoice.get("amount") or 0.0)
    max_factor = max(factors.values()) if factors else 0.0
    dominant_factor = max(factors, key=lambda name: factors[name]) if factors else None
    confidence = max(0.0, min(1.0, 0.5 + max_factor / 2.0))
    return {
        "invoice_id": invoice.get("invoice_id") or invoice.get("event_id"),
        "category": category,
        "intent": intent_id,
        "intent_id": intent_id,
        "intent_name": intent["name"],
        "description": intent["description"],
        "route": intent["route"],
        "confidence": round(confidence, 4),
        "intent_confidence": round(classified_intent.confidence, 4),
        "intent_category": classified_intent.category.value,
        "default_action": classified_intent.default_action,
        "intent_priority": classified_intent.priority,
        "classification": _classified_intent_payload(classified_intent),
        "evidence_panels": intent["evidence_panels"],
        "factors": factors,
        "amount": amount,
        "priority": round(amount * max_factor * 0.01, 2),
        "max_factor": round(max_factor, 4),
        "dominant_factor": dominant_factor,
        "source": source,
    }


def _classified_intent_payload(classified_intent) -> dict[str, Any]:
    payload = cast(dict[str, Any], classified_intent.model_dump())
    payload["intent"] = classified_intent.intent.value
    payload["category"] = classified_intent.category.value
    return payload


@router.get("/intents", response_model=CollectionResponse)
def intents() -> dict[str, Any]:
    return {
        "intents": [_intent_payload(intent.value) for intent in IntentType],
        "count": len(IntentType),
        "source": "s2p_domain_config",
    }


def _classify_from_inputs(
    invoice_id: str | None = None,
    category: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    invoice_id = invoice_id or payload.get("invoice_id") or payload.get("event_id")
    category = category or payload.get("category")
    if invoice_id is None:
        if category is None:
            raise HTTPException(status_code=422, detail="invoice_id or category is required")
        invoice = {
            "invoice_id": None,
            "category": category,
            "amount": payload.get("amount", 0.0),
            "factors": payload.get("factors", {}),
            **payload,
        }
    else:
        found_invoice = find_invoice(invoice_id)
        if found_invoice is None:
            raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
        invoice = found_invoice
    return _classify_invoice(invoice, category_override=category)


@router.get("/classify", response_model=GenericResponse)
def classify(
    invoice_id: str | None = None,
    category: str | None = Query(None),
) -> dict[str, Any]:
    return _classify_from_inputs(invoice_id=invoice_id, category=category)


@router.post("/classify", response_model=GenericResponse)
def classify_post(payload: dict[str, Any]) -> dict[str, Any]:
    return _classify_from_inputs(payload=payload)


@router.get("/queue", response_model=CollectionResponse)
def queue(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    entries = []
    for invoice in load_invoices():
        classified = _classify_invoice(invoice)
        entries.append(
            {
                "invoice_id": classified["invoice_id"],
                "intent": classified["intent"],
                "intent_id": classified["intent_id"],
                "priority": classified["priority"],
                "amount": classified["amount"],
                "category": classified["category"],
                "supplier": invoice.get("supplier_name") or invoice.get("supplier_id"),
                "supplier_id": invoice.get("supplier_id"),
                "route": classified["route"],
                "intent_category": classified["intent_category"],
                "default_action": classified["default_action"],
                "intent_priority": classified["intent_priority"],
                "dominant_factor": classified["dominant_factor"],
                "factors": classified["factors"],
            }
        )
    entries.sort(key=lambda item: item["priority"], reverse=True)
    return {
        "queue": entries[:limit],
        "items": entries[:limit],
        "total": len(entries),
        "showing": min(limit, len(entries)),
        "source": "synthetic_invoices.json",
    }
