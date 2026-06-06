"""Rule-based S2P Control Tower intent classifier."""

from __future__ import annotations

from typing import Any

from app.models.intents import ClassifiedIntent, INTENT_METADATA, IntentType


CATEGORY_INTENT: dict[str, IntentType] = {
    "price_variance": IntentType.triage_price,
    "quantity_mismatch": IntentType.triage_quantity,
    "duplicate_risk": IntentType.triage_duplicate,
    "contract_gap": IntentType.triage_contract,
    "format_compliance": IntentType.triage_format,
}


def classify_intent(invoice: dict[str, Any], query: str | None = None) -> ClassifiedIntent:
    if query is not None:
        query_result = _classify_query(query.lower())
        if query_result is not None:
            return query_result

    category = str(invoice.get("category") or "")
    if category in CATEGORY_INTENT:
        return _make_result(CATEGORY_INTENT[category], 0.86)

    action_result = _classify_action(_combined_text(invoice, query))
    if action_result is not None:
        return action_result

    return _make_result(IntentType.hold_review, 0.50)


def _classify_query(text: str) -> ClassifiedIntent | None:
    if not text:
        return None
    if _has_any(text, ("audit", "export", "download", "audit trail")) and _has_any(
        text,
        ("report", "export", "download", "trail"),
    ):
        return _make_result(IntentType.report_audit, 0.88)
    if _has_any(text, ("financial", "impact", "leakage", "savings")) and "report" in text:
        return _make_result(IntentType.report_financial, 0.86)
    if _has_any(text, ("batch", "bulk", "all invoices", "all")):
        return _make_result(IntentType.batch_process, 0.84)
    if _has_any(text, ("supplier", "vendor")):
        return _make_result(IntentType.query_supplier, 0.82)
    if _has_any(text, ("compliance", "sox", "tax", "policy")):
        return _make_result(IntentType.query_compliance, 0.82)
    if _has_any(text, ("conservation", "safety", "green")):
        return _make_result(IntentType.query_conservation, 0.82)
    if _has_any(text, ("invoice", "status", "where", "track")):
        return _make_result(IntentType.query_invoice, 0.78)
    return None


def _classify_action(text: str) -> ClassifiedIntent | None:
    if not text:
        return None
    if _has_any(text, ("auto_approve", "auto approve", "auto-approve")):
        return _make_result(IntentType.auto_approve, 0.82)
    if _has_any(text, ("hold", "review")):
        return _make_result(IntentType.hold_review, 0.76)
    if "buyer" in text:
        return _make_result(IntentType.escalate_buyer, 0.78)
    if "manager" in text:
        return _make_result(IntentType.escalate_manager, 0.78)
    if "specialist" in text:
        return _make_result(IntentType.refer_specialist, 0.78)
    return None


def _make_result(intent: IntentType, confidence: float) -> ClassifiedIntent:
    metadata = INTENT_METADATA[intent]
    return ClassifiedIntent(
        intent=intent,
        confidence=max(0.0, min(float(confidence), 1.0)),
        category=metadata["category"],
        description=str(metadata["description"]),
        default_action=metadata.get("default_action"),
        priority=metadata.get("priority"),
    )


def _combined_text(invoice: dict[str, Any], query: str | None) -> str:
    parts = [query or ""]
    for key in ("intent", "action", "action_hint", "request", "question", "message"):
        value = invoice.get(key)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).lower()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
