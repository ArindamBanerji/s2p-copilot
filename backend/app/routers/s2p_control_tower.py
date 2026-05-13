"""S2P Control Tower endpoints for intent classification and queue priority."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.domains.s2p.config import S2PDomainConfig
from app.domains.s2p.factors import compute_all_factors

router = APIRouter(prefix="/api/s2p/control-tower", tags=["s2p-control-tower"])


INTENTS: dict[str, dict[str, Any]] = {
    "invoice_price_variance": {
        "name": "invoice_price_variance",
        "description": "Invoice amount diverges from expected purchase order or commodity context.",
        "categories": ["price_variance"],
        "route": "insight",
        "evidence_panels": ["factor_fingerprint", "similar_invoices", "pvg_leakage"],
    },
    "invoice_match_failure": {
        "name": "invoice_match_failure",
        "description": "Invoice cannot be cleanly matched to purchase order or goods receipt evidence.",
        "categories": ["quantity_mismatch"],
        "route": "triage",
        "evidence_panels": ["process_context", "factor_fingerprint", "audit_trail"],
    },
    "invoice_duplicate_risk": {
        "name": "invoice_duplicate_risk",
        "description": "Invoice resembles a duplicate or repeated supplier exception.",
        "categories": ["duplicate_risk"],
        "route": "evidence",
        "evidence_panels": ["similar_invoices", "audit_trail", "supplier_heatmap"],
    },
    "contract_compliance_gap": {
        "name": "contract_compliance_gap",
        "description": "Contract, payment term, tax, or policy evidence needs review.",
        "categories": ["contract_gap"],
        "route": "evidence",
        "evidence_panels": ["rule_lifecycle", "compliance", "audit_trail"],
    },
    "format_compliance_issue": {
        "name": "format_compliance_issue",
        "description": "Invoice format, required field, or regulatory completeness issue.",
        "categories": ["format_compliance"],
        "route": "triage",
        "evidence_panels": ["factor_fingerprint", "process_context"],
    },
}

CATEGORY_INTENT = {
    "price_variance": "invoice_price_variance",
    "quantity_mismatch": "invoice_match_failure",
    "duplicate_risk": "invoice_duplicate_risk",
    "contract_gap": "contract_compliance_gap",
    "format_compliance": "format_compliance_issue",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _data_path(filename: str) -> Path:
    return _repo_root() / "data" / filename


def _load_invoices() -> list[dict[str, Any]]:
    try:
        data = json.loads(_data_path("synthetic_invoices.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [invoice for invoice in data if isinstance(invoice, dict)] if isinstance(data, list) else []


def _find_invoice(invoice_id: str) -> dict[str, Any] | None:
    for invoice in _load_invoices():
        if invoice.get("invoice_id") == invoice_id or invoice.get("event_id") == invoice_id:
            return invoice
    return None


def _intent_payload(intent_id: str) -> dict[str, Any]:
    intent = dict(INTENTS[intent_id])
    intent["intent_id"] = intent_id
    return intent


def _infer_intent(category: str, factors: dict[str, float]) -> tuple[str, str]:
    if category in CATEGORY_INTENT:
        return CATEGORY_INTENT[category], "category_mapping"
    if float(factors.get("duplicate_score", 0.0)) > 0.7:
        return "invoice_duplicate_risk", "factor_inference"
    if float(factors.get("amount_variance_ratio", 0.0)) > 0.3:
        return "invoice_price_variance", "factor_inference"
    if float(factors.get("match_status", 0.0)) > 0.65:
        return "invoice_match_failure", "factor_inference"
    if (
        float(factors.get("tax_regulatory_compliance", 0.0)) > 0.65
        or float(factors.get("payment_terms_impact", 0.0)) > 0.7
    ):
        return "contract_compliance_gap", "factor_inference"
    return "format_compliance_issue", "fallback"


def _classify_invoice(invoice: dict[str, Any], category_override: str | None = None) -> dict[str, Any]:
    category = category_override or str(invoice.get("category") or "")
    if category not in S2PDomainConfig.categories:
        raise HTTPException(status_code=422, detail=f"Unknown S2P category: {category}")
    factors = compute_all_factors({**invoice, "category": category})
    intent_id, source = _infer_intent(category, factors)
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
        "evidence_panels": intent["evidence_panels"],
        "factors": factors,
        "amount": amount,
        "priority": round(amount * max_factor * 0.01, 2),
        "max_factor": round(max_factor, 4),
        "dominant_factor": dominant_factor,
        "source": source,
    }


@router.get("/intents")
def intents() -> dict[str, Any]:
    return {
        "intents": [_intent_payload(intent_id) for intent_id in INTENTS],
        "count": len(INTENTS),
        "source": "s2p_domain_config",
    }


@router.get("/classify")
def classify(
    invoice_id: str | None = None,
    category: str | None = Query(None),
) -> dict[str, Any]:
    if invoice_id is None:
        if category is None:
            raise HTTPException(status_code=422, detail="invoice_id or category is required")
        invoice = {
            "invoice_id": None,
            "category": category,
            "amount": 0.0,
            "factors": {},
        }
    else:
        invoice = _find_invoice(invoice_id)
        if invoice is None:
            raise HTTPException(status_code=404, detail=f"Invoice {invoice_id} not found")
    return _classify_invoice(invoice, category_override=category)


@router.get("/queue")
def queue(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    entries = []
    for invoice in _load_invoices():
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
