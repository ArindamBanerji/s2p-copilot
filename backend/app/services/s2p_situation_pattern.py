"""S2P TraversalPattern adapter for invoice situation context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from copilot_sdk.situation import (
    SituationContext,
    TraversalEdge,
    TraversalNode,
    TypedIntent,
)

from app.routers.s2p_data_helpers import find_invoice
from app.services.receipt_store import get_receipt_store
from app.services.s2p_context_builder import S2PContextBuilder
from app.services.s2p_evidence_templates import evidence_context_from_record


@dataclass
class S2PInvoiceTraversalPattern:
    """Assemble S2P invoice context through existing store and fixture data."""

    domain: str = "s2p"
    name: str = "s2p_invoice_context"
    default_max_depth: int = 3
    scorer: Any = None

    def supports(self, intent: TypedIntent) -> bool:
        if intent.domain != self.domain:
            return False
        subject = (intent.subject or "").lower()
        context = _intent_context(intent)
        return any(
            [
                "invoice" in subject,
                "decision" in subject,
                "purchase" in subject,
                "supplier" in subject,
                bool(
                    _first(
                        context,
                        "invoice_id",
                        "event_id",
                        "source_invoice_id",
                        "source_event_id",
                        "decision_id",
                        "supplier_id",
                        "supplier",
                        "supplier_name",
                        "po_id",
                        "purchase_order",
                        "po_number",
                        "contract_ref",
                        "contract_id",
                        "category",
                    )
                ),
            ]
        )

    def traverse(
        self,
        intent: TypedIntent,
        *,
        graph_store: Any = None,
        max_depth: int = 3,
    ) -> SituationContext:
        max_depth = max(int(max_depth), 0)
        warnings: list[str] = []
        context = _intent_context(intent)
        invoice_id = _first(context, "invoice_id", "event_id", "source_invoice_id", "source_event_id")
        decision_id = _first(context, "decision_id") or intent.decision_id
        invoice = find_invoice(str(invoice_id)) if invoice_id else None
        decision = _find_decision(graph_store, decision_id, invoice_id)
        if invoice is None and decision is not None:
            invoice_id = _decision_value(decision, "invoice_id", "source_invoice_id", "entity_id")
            invoice = find_invoice(str(invoice_id)) if invoice_id else None

        base_record = {}
        if decision:
            base_record.update(_flatten_decision(decision))
        if invoice:
            base_record.update(invoice)
        base_record.update({key: value for key, value in context.items() if value is not None})

        builder = S2PContextBuilder(scorer=self.scorer, graph_store=graph_store)
        built = builder.build_invoice_context(
            invoice_id=str(invoice_id or base_record.get("invoice_id")) if invoice_id or base_record.get("invoice_id") else None,
            category=str(base_record.get("category")) if base_record.get("category") else None,
            decision_id=str(decision_id) if decision_id else None,
            context_data=base_record,
            max_depth=max_depth,
        )
        nodes: list[TraversalNode] = list(built.nodes)
        edges: list[TraversalEdge] = list(built.edges)
        evidence_chain: list[dict[str, Any]] = list(built.evidence_chain)
        warnings.extend(built.warnings)
        receipts = _receipt_context(invoice_id, decision_id)
        if receipts:
            evidence_chain.append({"type": "receipts", "count": len(receipts), "receipts": receipts})

        context_used = built.metadata.get("context_used") or (evidence_context_from_record(base_record) if base_record else dict(context))
        return SituationContext(
            domain=self.domain,
            decision_id=str(decision_id) if decision_id else None,
            intent=intent,
            pattern_name=self.name,
            nodes=nodes,
            edges=edges,
            evidence_chain=evidence_chain,
            max_depth=max_depth,
            truncated=False,
            warnings=warnings,
            metadata={
                **built.metadata,
                "context_used": context_used,
                "degraded": bool(warnings),
                "p38_context_builder": True,
            },
        )


def _find_decision(graph_store: Any, decision_id: Any, invoice_id: Any) -> dict[str, Any] | None:
    if graph_store is None:
        return None
    get_decision = getattr(graph_store, "get_decision", None)
    if callable(get_decision) and decision_id:
        try:
            decision = get_decision(str(decision_id))
            if isinstance(decision, dict):
                return decision
        except Exception:
            return None
    for decision in _linked_decisions(graph_store, invoice_id):
        return decision
    get_all = getattr(graph_store, "get_all_decisions", None)
    if not callable(get_all):
        return None
    for args in ((getattr(graph_store, "domain", "s2p"),), ()):
        try:
            rows = get_all(*args)
        except TypeError:
            continue
        except Exception:
            return None
        for row in rows:
            if isinstance(row, dict) and invoice_id and _matches_invoice(row, str(invoice_id)):
                return row
        break
    return None


def _linked_decisions(graph_store: Any, invoice_id: Any) -> list[dict[str, Any]]:
    if not invoice_id:
        return []
    get_links = getattr(graph_store, "get_decision_links", None)
    get_decision = getattr(graph_store, "get_decision", None)
    if not callable(get_links) or not callable(get_decision):
        return []
    try:
        links = get_links()
    except Exception:
        return []
    decisions: list[dict[str, Any]] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        if link.get("edge_type") != "DECIDED_ON" or str(link.get("entity_id")) != str(invoice_id):
            continue
        try:
            decision = get_decision(str(link.get("decision_id")))
        except Exception:
            continue
        if isinstance(decision, dict):
            decisions.append(decision)
    return decisions


def _receipt_context(invoice_id: Any, decision_id: Any) -> list[dict[str, Any]]:
    store = get_receipt_store()
    receipts: list[dict[str, Any]] = []
    if invoice_id:
        try:
            receipts.extend(store.get_for_invoice(str(invoice_id)))
        except Exception:
            pass
    if decision_id:
        try:
            receipts.extend(store.get_for_decision(str(decision_id)))
        except Exception:
            pass
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for receipt in receipts:
        marker = str(receipt.get("receipt_id") or receipt)
        if marker not in seen:
            seen.add(marker)
            unique.append(receipt)
    return unique


def _flatten_decision(decision: dict[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata") if isinstance(decision.get("metadata"), dict) else {}
    return {**metadata, **decision}


def _matches_invoice(decision: dict[str, Any], invoice_id: str) -> bool:
    flat = _flatten_decision(decision)
    return invoice_id in {
        str(flat.get("invoice_id")),
        str(flat.get("source_invoice_id")),
        str(flat.get("entity_id")),
        str(flat.get("decision_id")),
    }


def _decision_value(decision: dict[str, Any], *keys: str) -> Any:
    flat = _flatten_decision(decision)
    return _first(flat, *keys)


def _intent_context(intent: TypedIntent) -> dict[str, Any]:
    context: dict[str, Any] = {}
    metadata = intent.metadata if isinstance(intent.metadata, dict) else {}
    raw_payload = metadata.get("raw_payload") if isinstance(metadata.get("raw_payload"), dict) else {}
    if raw_payload:
        payload = raw_payload.get("payload")
        if isinstance(payload, dict):
            context.update(payload)
        context.update(
            {
                key: value
                for key, value in raw_payload.items()
                if key not in _RAW_SIGNAL_ENVELOPE_KEYS
            }
        )
    snapshot = intent.context_snapshot
    if snapshot is not None and isinstance(snapshot.facts, dict):
        context.update(snapshot.facts)
    context.update({key: value for key, value in metadata.items() if key != "raw_payload"})
    context.update(intent.scope or {})
    if intent.decision_id and "decision_id" not in context:
        context["decision_id"] = intent.decision_id
    if intent.source_event_id and not _first(context, "invoice_id", "event_id", "source_invoice_id", "source_event_id"):
        context["source_event_id"] = intent.source_event_id
    return context


_RAW_SIGNAL_ENVELOPE_KEYS = {
    "domain",
    "signal_type",
    "intent_type",
    "verb",
    "subject",
    "scope",
    "source_event_id",
    "decision_id",
    "trace_id",
    "policies",
    "context_snapshot",
    "created_at",
    "payload",
    "metadata",
}


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None

