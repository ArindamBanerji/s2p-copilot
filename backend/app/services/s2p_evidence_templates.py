"""S2P L1 evidence template rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from copilot_sdk.situation.templates import SafeTemplateRenderer

from app.domains.s2p.config import S2PDomainConfig


S2P_FACTOR_MAP: dict[str, list[str]] = {
    "price_variance": ["amount_variance_ratio", "commodity_index_correlation", "payment_terms_impact"],
    "quantity_mismatch": ["match_status", "amount_variance_ratio"],
    "duplicate_risk": ["duplicate_score", "supplier_exception_history"],
    "contract_gap": ["match_status", "payment_terms_impact"],
    "format_compliance": ["tax_regulatory_compliance", "supplier_exception_history"],
}


@dataclass(frozen=True)
class EvidenceTemplate:
    category: str
    template: str
    required_fields: list[str] = field(default_factory=list)
    factors_used: list[str] = field(default_factory=list)
    audience: str = "L1"

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "audience": self.audience,
            "template": self.template,
            "required_fields": list(self.required_fields),
            "factors_used": list(self.factors_used),
        }


@dataclass(frozen=True)
class RenderedEvidence:
    category: str
    audience: str
    text: str
    factors_used: list[str]
    confidence: float
    missing_fields: list[str] = field(default_factory=list)
    context_used: dict[str, Any] = field(default_factory=dict)
    template: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "audience": self.audience,
            "text": self.text,
            "rendered": self.text,
            "factors_used": list(self.factors_used),
            "confidence": self.confidence,
            "confidence_pct": _confidence_pct(self.confidence),
            "missing_fields": list(self.missing_fields),
            "context_used": _json_safe(self.context_used),
            "template": self.template,
        }


S2P_TEMPLATES: dict[str, EvidenceTemplate] = {
    "price_variance": EvidenceTemplate(
        category="price_variance",
        template=(
            "{variance_pct:.1f}% price delta on invoice {invoice_id}. "
            "{commodity} moved {commodity_delta:.1f}% over {lookback} days. "
            "Contract {ref} {allows_blocks} pass-through up to {threshold:.1f}%. "
            "{within_exceeds} bounds. -> {action}. Confidence: {confidence_pct}."
        ),
        required_fields=[
            "invoice_id",
            "variance_pct",
            "commodity",
            "commodity_delta",
            "ref",
            "action",
        ],
        factors_used=S2P_FACTOR_MAP["price_variance"],
    ),
    "quantity_mismatch": EvidenceTemplate(
        category="quantity_mismatch",
        template=(
            "Invoice qty {inv_qty} vs PO {po_qty}; GR confirms {gr_qty} received. "
            "Delta {delta}. {match_status}. -> {action}. Confidence: {confidence_pct}."
        ),
        required_fields=["inv_qty", "po_qty", "gr_qty", "delta", "action"],
        factors_used=S2P_FACTOR_MAP["quantity_mismatch"],
    ),
    "duplicate_risk": EvidenceTemplate(
        category="duplicate_risk",
        template=(
            "Invoice {invoice_id} from {supplier}. Similar candidate {match_id} dated "
            "{match_date}, amount {match_amt}, similarity {similarity:.1f}%. "
            "{verdict}. -> {action}. Confidence: {confidence_pct}."
        ),
        required_fields=["invoice_id", "supplier", "match_id", "match_date", "match_amt", "action"],
        factors_used=S2P_FACTOR_MAP["duplicate_risk"],
    ),
    "contract_gap": EvidenceTemplate(
        category="contract_gap",
        template=(
            "PO {po_id}. Contract {ref} covers {scope}; coverage {covered_pct:.1f}%. "
            "Gap: {gap_items}. -> {action}. Confidence: {confidence_pct}."
        ),
        required_fields=["po_id", "ref", "scope", "covered_pct", "gap_items", "action"],
        factors_used=S2P_FACTOR_MAP["contract_gap"],
    ),
    "format_compliance": EvidenceTemplate(
        category="format_compliance",
        template=(
            "Invoice from {supplier} fails {n_rules:.0f} format rules. "
            "Issues: {issues}. Historical compliance: {compliance_pct:.1f}%. "
            "-> {action}. Confidence: {confidence_pct}."
        ),
        required_fields=["supplier", "n_rules", "issues", "compliance_pct", "action"],
        factors_used=S2P_FACTOR_MAP["format_compliance"],
    ),
}


class S2PEvidenceEngine:
    def __init__(self, templates: dict[str, EvidenceTemplate] | None = None) -> None:
        self._templates = dict(templates or S2P_TEMPLATES)
        self._renderer = SafeTemplateRenderer()

    def available_categories(self) -> list[str]:
        return list(S2PDomainConfig.categories)

    def get_template(self, category: str) -> EvidenceTemplate | None:
        return self._templates.get(category)

    def render(
        self,
        category: str,
        context: dict[str, Any],
        action: str,
        confidence: float,
        audience: str = "L1",
    ) -> RenderedEvidence:
        normalized_context = _context_defaults(context)
        normalized_context["action"] = action or normalized_context.get("action") or "unknown"
        normalized_context["confidence"] = _float(confidence, 0.0)
        normalized_context["confidence_pct"] = _confidence_pct(normalized_context["confidence"])
        template = self._templates.get(category)
        if template is None:
            text = (
                f"S2P situation {category or 'unknown'} requires review. "
                f"-> {normalized_context['action']}. Confidence: {normalized_context['confidence_pct']}."
            )
            return RenderedEvidence(
                category=category or "unknown",
                audience=audience,
                text=text,
                factors_used=[],
                confidence=normalized_context["confidence"],
                missing_fields=[],
                context_used=normalized_context,
                template="",
            )

        result = self._renderer.render(
            template.template,
            normalized_context,
            defaults=_template_defaults(),
            audience=audience,
        )
        missing = sorted(set(result.missing_variables) | {
            field for field in template.required_fields if _is_missing(context.get(field))
        })
        return RenderedEvidence(
            category=category,
            audience=audience,
            text=result.rendered,
            factors_used=list(template.factors_used),
            confidence=normalized_context["confidence"],
            missing_fields=missing,
            context_used=result.variables,
            template=template.template,
        )

    def render_from_decision(
        self,
        decision: dict[str, Any],
        outcome: dict[str, Any] | None = None,
    ) -> RenderedEvidence:
        context = evidence_context_from_record(decision)
        if outcome:
            context.update({key: value for key, value in outcome.items() if value is not None})
        category = str(context.get("category") or decision.get("category") or "unknown")
        action = str(
            context.get("action")
            or decision.get("recommended_action")
            or decision.get("action")
            or "unknown"
        )
        confidence = _float(context.get("confidence") or decision.get("confidence"), 0.0)
        return self.render(category, context, action, confidence)


def evidence_context_from_record(record: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = record.get("metadata")
    metadata: dict[str, Any] = (
        cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
    )
    raw_factors = record.get("factors")
    factors: dict[str, Any] = (
        cast(dict[str, Any], raw_factors) if isinstance(raw_factors, dict) else {}
    )
    context = {**metadata, **{key: value for key, value in record.items() if value is not None}}
    context["factors"] = dict(factors)
    invoice_id = context.get("invoice_id") or context.get("event_id") or context.get("entity_id") or "unknown"
    supplier = context.get("supplier_name") or context.get("supplier_id") or context.get("supplier") or "unknown"
    amount_variance = _factor(context, "amount_variance_ratio")
    commodity_delta = _factor(context, "commodity_index_correlation")
    duplicate_score = _factor(context, "duplicate_score")
    match_score = _factor(context, "match_status")
    tax_score = _factor(context, "tax_regulatory_compliance")
    inv_qty = _line_item_quantity(context)
    po_qty = _number(context.get("po_qty"), round(inv_qty * 0.96, 2) if inv_qty is not None else 0.0)
    gr_qty = _number(context.get("gr_qty"), po_qty)
    context.update(
        {
            "invoice_id": invoice_id,
            "supplier": supplier,
            "variance_pct": round(amount_variance * 100.0, 1),
            "commodity": context.get("commodity") or "unknown",
            "commodity_delta": round(commodity_delta * 100.0, 1),
            "lookback": _number(context.get("lookback"), 30.0),
            "ref": context.get("contract_ref") or context.get("contract_id") or "unknown",
            "allows_blocks": "allows" if amount_variance <= 0.2 else "blocks",
            "threshold": _number(context.get("threshold"), 20.0),
            "within_exceeds": "within" if amount_variance <= 0.2 else "exceeds",
            "score": round(max([_float(value, 0.0) for value in factors.values()] or [0.0]), 3),
            "inv_qty": inv_qty if inv_qty is not None else 0.0,
            "po_qty": po_qty,
            "delta": round((inv_qty or 0.0) - po_qty, 2),
            "gr_qty": gr_qty,
            "match_status": "matched" if match_score >= 0.7 else "mismatch requires review",
            "match_id": context.get("match_id") or f"{invoice_id}-PRIOR",
            "match_date": context.get("match_date") or context.get("invoice_date") or "unknown date",
            "match_amt": context.get("match_amt") or context.get("amount") or 0.0,
            "similarity": round(duplicate_score * 100.0, 1),
            "verdict": "possible duplicate" if duplicate_score >= 0.5 else "no duplicate pattern",
            "po_id": context.get("po_id") or context.get("po_number") or "unknown",
            "scope": context.get("scope") or context.get("commodity") or context.get("category") or "unknown",
            "covered_pct": round(match_score * 100.0, 1),
            "gap_items": context.get("gap_items") or ("line-item coverage" if match_score < 0.8 else "none"),
            "n_rules": _number(context.get("n_rules"), 1.0 if tax_score >= 0.3 else 0.0),
            "issues": context.get("issues") or ("tax/regulatory completeness" if tax_score >= 0.3 else "none"),
            "compliance_pct": round(max(0.0, min(1.0, 1.0 - tax_score)) * 100.0, 1),
            "category": context.get("category") or "unknown",
        }
    )
    return context


def _template_defaults() -> dict[str, Any]:
    return {
        "unknown": "unknown",
        "invoice_id": "unknown",
        "supplier": "unknown",
        "commodity": "unknown",
        "ref": "unknown",
        "action": "unknown",
        "confidence_pct": "0%",
    }


def _context_defaults(context: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(context or {})
    for key, value in list(normalized.items()):
        if value is None:
            normalized[key] = "unknown"
    return normalized


def _factor(context: dict[str, Any], name: str) -> float:
    factors = context.get("factors")
    if isinstance(factors, dict):
        return _float(factors.get(name), 0.0)
    return _float(context.get(name), 0.0)


def _line_item_quantity(context: dict[str, Any]) -> float | None:
    value = context.get("inv_qty")
    if value is not None:
        return _float(value, 0.0)
    items = context.get("line_items")
    if not isinstance(items, list):
        return None
    total = 0.0
    found = False
    for item in items:
        if isinstance(item, dict):
            total += _float(item.get("quantity"), 0.0)
            found = True
    return round(total, 2) if found else None


def _confidence_pct(confidence: float) -> str:
    return f"{round(max(0.0, min(float(confidence), 1.0)) * 100.0):.0f}%"


def _number(value: Any, default: float) -> float:
    return _float(value, default)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _is_missing(value: Any) -> bool:
    return value is None or value == ""


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
