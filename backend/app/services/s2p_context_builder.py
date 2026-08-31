"""Read-only S2P evidence context builder for Situation Analyzer output."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from copilot_sdk.situation import TraversalEdge, TraversalNode
from copilot_sdk.graph.protocol import GraphStore

from app.domains.s2p.config import S2PDomainConfig
from app.graph.s2p_graph_reader import S2PGraphReader
from app.routers.s2p_data_helpers import find_invoice, load_suppliers
from app.services.s2p_evidence_templates import evidence_context_from_record
from app.services.s2p_enrichment import DOMAIN as S2P_ENRICHMENT_DOMAIN
from app.services.s2p_enrichment import ENTITY_TYPE as S2P_ENRICHMENT_ENTITY_TYPE
from app.services.s2p_enrichment import NAMESPACE as S2P_ENRICHMENT_NAMESPACE
from app.services.s2p_enrichment import serialize_provenanced_value

PROVENANCE_BY_SOURCE: dict[str, dict[str, Any]] = {
    "fixture": {
        "provenance_label": "fixture context · integration pending",
        "provenance_tier": "context",
        "integration_status": "pending",
        "measured": False,
        "display_prefix": "░░ context",
    },
    "graph_store": {
        "provenance_label": "decision history · GraphStore read",
        "provenance_tier": "context",
        "integration_status": "configured",
        "measured": True,
        "verified": False,
        "display_prefix": "decision history",
    },
    "scorer": {
        "provenance_label": "scorer state · learned from verifications",
        "provenance_tier": "learned",
        "integration_status": "configured",
        "measured": True,
        "display_prefix": "██ learned",
    },
    "context": {
        "provenance_label": "request context · integration pending",
        "provenance_tier": "context",
        "integration_status": "pending",
        "measured": False,
        "display_prefix": "░░ context",
    },
    "unavailable": {
        "provenance_label": "source unavailable · integration pending",
        "provenance_tier": "unavailable",
        "integration_status": "pending",
        "measured": False,
        "display_prefix": "integration pending",
    },
}


@dataclass
class S2PContextBuildResult:
    nodes: list[TraversalNode] = field(default_factory=list)
    edges: list[TraversalEdge] = field(default_factory=list)
    evidence_chain: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class S2PContextBuilder:
    """Build fixture/scorer/GraphStore-backed context without graph mutation."""

    def __init__(
        self,
        *,
        scorer: Any = None,
        graph_store: Any = None,
        reader: S2PGraphReader | None = None,
        fixtures: dict[str, Any] | None = None,
    ) -> None:
        self.scorer = scorer
        self.graph_store = graph_store
        self.reader = reader or (S2PGraphReader(store=graph_store) if graph_store is not None else None)
        self.fixtures = dict(fixtures or {})

    def build_invoice_context(
        self,
        *,
        invoice_id: str | None,
        category: str | None,
        decision_id: str | None,
        context_data: dict[str, Any],
        max_depth: int = 3,
    ) -> S2PContextBuildResult:
        max_depth = max(int(max_depth), 0)
        context = dict(context_data or {})
        invoice = self._invoice(invoice_id)
        if invoice:
            merged = {**invoice, **context}
        else:
            merged = dict(context)
            if invoice_id and "invoice_id" not in merged:
                merged["invoice_id"] = invoice_id
        raw_metadata = merged.get("metadata")
        metadata: dict[str, Any] = (
            cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
        )
        merged = {**metadata, **merged}
        if category and "category" not in merged:
            merged["category"] = category

        result = S2PContextBuildResult()
        invoice_node_id = self._add_invoice_context(result, merged, invoice, max_depth)
        self._add_target_decision_context(result, merged, decision_id, invoice_node_id, max_depth)
        supplier_node_id = self.build_supplier_context(result, merged, invoice_node_id, max_depth)
        self.build_po_contract_context(result, merged, invoice_node_id, max_depth)
        if invoice:
            self._add_category_context(result, merged, supplier_node_id, max_depth)
        similar = self.find_similar_decisions(
            supplier_id=_first(merged, "supplier_id", "supplier"),
            category=str(merged.get("category") or category or ""),
            decision_id=decision_id,
            max_results=3,
        )
        similarity_criteria = _similarity_criteria(
            supplier_id=_first(merged, "supplier_id", "supplier"),
            category=str(merged.get("category") or category or ""),
            decision_id=decision_id,
        )
        if similar:
            self._add_similar_decision_context(result, similar, invoice_node_id, max_depth, similarity_criteria)
        elif self.graph_store is None:
            result.warnings.append("decision history unavailable: graph_store not provided")
        else:
            result.warnings.append("decision history unavailable from existing GraphStore read methods")

        self.build_category_centroid_context(result, merged, max_depth)
        context_used = evidence_context_from_record(merged) if merged else dict(context)
        result.metadata.update(
            {
                "context_used": context_used,
                "data_sources": _data_sources(result.nodes),
                "degraded": bool(result.warnings),
                "native_graph_traversal_deferred": True,
                "similarity_criteria": similarity_criteria,
                "similarity_label": "same supplier + same category",
            }
        )
        return result

    def find_similar_decisions(
        self,
        *,
        supplier_id: Any,
        category: str | None,
        decision_id: str | None = None,
        max_results: int = 3,
    ) -> list[dict[str, Any]]:
        if self.reader is None or not supplier_id or not category:
            return []
        rows = self._decision_rows(str(category))
        matches: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            flat = _flatten(row)
            if decision_id and str(flat.get("decision_id")) == str(decision_id):
                continue
            if str(flat.get("category") or "") != str(category):
                continue
            if str(_first(flat, "supplier_id", "supplier") or "") != str(supplier_id):
                continue
            matches.append(row)
        matches.sort(key=_decision_timestamp, reverse=True)
        return matches[: max(0, int(max_results))]

    def build_supplier_context(
        self,
        result: S2PContextBuildResult,
        context: dict[str, Any],
        invoice_node_id: str | None,
        max_depth: int,
    ) -> str | None:
        supplier_id = _first(context, "supplier_id", "supplier")
        supplier_name = _first(context, "supplier_name", "supplier")
        supplier = self._supplier(supplier_id, supplier_name)
        if not supplier_id and supplier:
            supplier_id = supplier.get("supplier_id")
        if not supplier_name and supplier:
            supplier_name = supplier.get("name")
        if not supplier_id:
            result.warnings.append("supplier unavailable from fixture/context")
            return None
        if max_depth < 1:
            return None
        properties = _clean(
            _with_provenance(
                "fixture",
                {
                    "supplier_id": supplier_id,
                    "supplier_name": supplier_name,
                    "otif_score": supplier.get("otif_score") if supplier else None,
                    "exception_rate": supplier.get("exception_rate") if supplier else None,
                    "recent_trend": supplier.get("recent_trend") if supplier else None,
                },
                label="supplier context · integration pending",
            )
        )
        enrichment = self._supplier_enrichment(str(supplier_id))
        if enrichment:
            properties["enrichment"] = enrichment
        node_id = str(supplier_id)
        result.nodes.append(
            TraversalNode(
                id=node_id,
                type="supplier",
                label=str(supplier_name or supplier_id),
                properties=properties,
                depth=1,
                source="fixture",
            )
        )
        result.evidence_chain.append(
            _with_provenance(
                "fixture",
                {"type": "supplier_context", "supplier_id": node_id},
                label="supplier context · integration pending",
            )
        )
        if invoice_node_id:
            result.edges.append(
                TraversalEdge(invoice_node_id, node_id, "FROM_SUPPLIER", _with_provenance("fixture"), depth=1)
            )
        return node_id

    def _supplier_enrichment(self, supplier_id: str) -> dict[str, Any]:
        if not isinstance(self.graph_store, GraphStore):
            return {}
        try:
            values = self.graph_store.read_entity_enrichment(
                domain=S2P_ENRICHMENT_DOMAIN,
                entity_type=S2P_ENRICHMENT_ENTITY_TYPE,
                entity_id=str(supplier_id),
                namespace=S2P_ENRICHMENT_NAMESPACE,
            )
        except Exception:
            return {}
        if not isinstance(values, dict):
            return {}
        return {
            str(name): serialize_provenanced_value(value)
            for name, value in sorted(values.items())
            if hasattr(value, "source") and hasattr(value, "provenance_tier")
        }

    def build_po_contract_context(
        self,
        result: S2PContextBuildResult,
        context: dict[str, Any],
        invoice_node_id: str | None,
        max_depth: int,
    ) -> None:
        po_id = _first(context, "po_id", "po_number", "purchase_order", "po")
        if po_id and max_depth >= 2:
            result.nodes.append(
                TraversalNode(
                    id=str(po_id),
                    type="purchase_order",
                    label="Purchase Order",
                    properties=_clean(
                        _with_provenance(
                            "fixture",
                            {
                                "po_id": po_id,
                                "po_date": _metadata_value(context, "po_date"),
                                "gr_date": _metadata_value(context, "gr_date"),
                                "contractual_lead_time_days": _metadata_value(
                                    context, "contractual_lead_time_days"
                                ),
                            },
                            label="PO context · integration pending",
                        )
                    ),
                    depth=2,
                    source="fixture",
                )
            )
            result.evidence_chain.append(
                _with_provenance(
                    "fixture",
                    {"type": "po_context", "po_id": str(po_id)},
                    label="PO context · integration pending",
                )
            )
            if invoice_node_id:
                result.edges.append(
                    TraversalEdge(invoice_node_id, str(po_id), "MATCHES_PO", _with_provenance("fixture"), depth=2)
                )
        elif max_depth >= 2:
            result.warnings.append("PO unavailable from fixture/context")
            result.evidence_chain.append(_with_provenance("unavailable", {"type": "po_unavailable"}))

        contract_ref = _first(context, "contract_ref", "contract_id", "ref")
        if contract_ref and max_depth >= 3:
            result.nodes.append(
                TraversalNode(
                    id=str(contract_ref),
                    type="contract",
                    label="Contract Reference",
                    properties=_with_provenance(
                        "fixture",
                        {"contract_ref": contract_ref},
                        label="contract reference · integration pending",
                    ),
                    depth=3,
                    source="fixture",
                )
            )
            result.evidence_chain.append(
                _with_provenance(
                    "fixture",
                    {"type": "contract_reference", "contract_ref": str(contract_ref)},
                    label="contract reference · integration pending",
                )
            )
            if po_id:
                result.edges.append(
                    TraversalEdge(str(po_id), str(contract_ref), "REFERENCES_CONTRACT", _with_provenance("fixture"), depth=3)
                )
            result.warnings.append("contract details unavailable; only fixture contract_ref is present")
            result.evidence_chain.append(
                _with_provenance(
                    "unavailable",
                    {"type": "contract_terms_unavailable", "contract_ref": str(contract_ref)},
                    label="contract terms unavailable · integration pending",
                )
            )
        elif max_depth >= 3:
            result.warnings.append("contract details unavailable from fixture/context")
            result.evidence_chain.append(
                _with_provenance(
                    "unavailable",
                    {"type": "contract_terms_unavailable"},
                    label="contract terms unavailable · integration pending",
                )
            )

    def build_category_centroid_context(
        self,
        result: S2PContextBuildResult,
        context: dict[str, Any],
        max_depth: int,
    ) -> None:
        category = context.get("category")
        action = _first(context, "ground_truth_action", "recommended_action", "action")
        if not category or not action or max_depth < 2:
            result.warnings.append("centroid context unavailable: category/action missing")
            return
        get_centroid = getattr(self.scorer, "get_centroid", None)
        if not callable(get_centroid):
            result.warnings.append("centroid context unavailable: scorer public get_centroid not available")
            return
        try:
            centroid = get_centroid(str(category), str(action))
        except Exception:
            result.warnings.append("centroid context unavailable from scorer")
            return
        if not isinstance(centroid, list):
            result.warnings.append("centroid context unavailable from scorer")
            return
        result.nodes.append(
            TraversalNode(
                id=f"centroid:{category}:{action}",
                type="centroid",
                label="Scorer Centroid",
                properties=_with_provenance(
                    "scorer",
                    {
                        "category": category,
                        "action": action,
                        "factor_names": list(S2PDomainConfig.factors),
                        "centroid": list(centroid),
                    },
                ),
                depth=2,
                source="scorer",
            )
        )
        result.evidence_chain.append(
            _with_provenance(
                "scorer",
                {
                    "type": "centroid_context",
                    "category": str(category),
                    "action": str(action),
                },
            )
        )

    def _add_invoice_context(
        self,
        result: S2PContextBuildResult,
        context: dict[str, Any],
        invoice: dict[str, Any] | None,
        max_depth: int,
    ) -> str | None:
        invoice_id = _first(context, "invoice_id", "event_id", "source_invoice_id", "source_event_id")
        if not invoice_id:
            result.warnings.append("invoice_id unavailable; context is limited")
            return None
        if not invoice:
            result.warnings.append("invoice fixture unavailable; using supplied intent context only")
            return None
        if max_depth < 0:
            return None
        properties = _clean(
            _with_provenance(
                "fixture",
                {
                    "invoice_id": invoice_id,
                    "amount": context.get("amount"),
                    "category": context.get("category"),
                    "currency": context.get("currency"),
                    "po_number": context.get("po_number"),
                },
                label="invoice fixture · integration pending",
            )
        )
        node_id = str(invoice_id)
        result.nodes.append(
            TraversalNode(
                id=node_id,
                type="invoice",
                label="Invoice",
                properties=properties,
                depth=0,
                source="fixture",
            )
        )
        result.evidence_chain.append(
            _with_provenance(
                "fixture",
                {
                    "type": "invoice",
                    "id": node_id,
                    "category": context.get("category"),
                    "action": context.get("ground_truth_action")
                    or context.get("recommended_action")
                    or context.get("action"),
                },
                label="invoice fixture · integration pending",
            )
        )
        return node_id

    def _add_category_context(
        self,
        result: S2PContextBuildResult,
        context: dict[str, Any],
        supplier_node_id: str | None,
        max_depth: int,
    ) -> None:
        category = context.get("category")
        if not category or max_depth < 2:
            return
        node_id = f"category:{category}"
        result.nodes.append(
            TraversalNode(
                id=node_id,
                type="category",
                label=str(category),
                properties=_with_provenance(
                    "fixture",
                    {"category": category},
                    label="category context · integration pending",
                ),
                depth=2,
                source="fixture",
            )
        )
        if supplier_node_id:
            result.edges.append(
                TraversalEdge(supplier_node_id, node_id, "SUPPLIES_CATEGORY", _with_provenance("fixture"), depth=2)
            )

    def _add_target_decision_context(
        self,
        result: S2PContextBuildResult,
        context: dict[str, Any],
        decision_id: str | None,
        invoice_node_id: str | None,
        max_depth: int,
    ) -> None:
        if not decision_id or max_depth < 1 or self.graph_store is None:
            return
        decision = self._verified_decision(decision_id)
        if decision is None:
            result.warnings.append("decision_id provided but no GraphStore decision record was found")
            return
        flat = _flatten(decision)
        properties = _clean(
            _with_decision_provenance(
                flat,
                {
                    "decision_id": decision_id,
                    "category": flat.get("category"),
                    "confidence": flat.get("confidence"),
                    "recommended_action": _first(flat, "recommended_action", "action"),
                },
            )
        )
        result.nodes.append(
            TraversalNode(
                id=str(decision_id),
                type="decision",
                label="Decision",
                properties=properties,
                depth=1,
                source="graph_store",
            )
        )
        if invoice_node_id:
            result.edges.append(
                TraversalEdge(str(decision_id), invoice_node_id, "DECIDED_ON", _with_provenance("graph_store"), depth=1)
            )

    def _verified_decision(self, decision_id: str) -> dict[str, Any] | None:
        if self.reader is None:
            return None
        decision = self.reader.get_decision(str(decision_id))
        if not isinstance(decision, dict):
            return None
        flat = _flatten(decision)
        if str(flat.get("decision_id") or "") != str(decision_id):
            return None
        return decision

    def _add_similar_decision_context(
        self,
        result: S2PContextBuildResult,
        decisions: list[dict[str, Any]],
        invoice_node_id: str | None,
        max_depth: int,
        similarity_criteria: dict[str, Any],
    ) -> None:
        if max_depth < 2:
            return
        for decision in decisions:
            flat = _flatten(decision)
            decision_id = flat.get("decision_id")
            if not decision_id:
                continue
            node_id = f"decision:{decision_id}"
            result.nodes.append(
                TraversalNode(
                    id=node_id,
                    type="similar_decision",
                    label="Similar Decision",
                    properties=_clean(
                        _with_decision_provenance(
                            flat,
                            {
                                "decision_id": decision_id,
                                "category": flat.get("category"),
                                "supplier_id": _first(flat, "supplier_id", "supplier"),
                                "recommended_action": _first(flat, "recommended_action", "action"),
                                "created_at": _first(flat, "created_at", "timestamp"),
                                "similarity_criteria": dict(similarity_criteria),
                                "similarity_label": "same supplier + same category",
                            },
                        )
                    ),
                    depth=2,
                    source="graph_store",
                )
            )
            if invoice_node_id:
                result.edges.append(
                    TraversalEdge(invoice_node_id, node_id, "SIMILAR_TO_DECISION", _with_provenance("graph_store"), depth=2)
                )
        result.evidence_chain.append(
            _with_similar_decisions_provenance(
                decisions,
                {
                    "type": "similar_decisions",
                    "count": len(decisions),
                    "similarity_criteria": dict(similarity_criteria),
                    "similarity_label": "same supplier + same category",
                },
            )
        )

    def _decision_rows(self, category: str) -> list[dict[str, Any]]:
        if self.reader is None:
            return []
        return cast(list[dict[str, Any]], self.reader.get_decisions(category=category, limit=400))

    def _invoice(self, invoice_id: str | None) -> dict[str, Any] | None:
        if not invoice_id:
            return None
        invoices = self.fixtures.get("invoices")
        if isinstance(invoices, list):
            for invoice in invoices:
                if isinstance(invoice, dict) and invoice.get("invoice_id") == invoice_id:
                    return dict(invoice)
        invoice = find_invoice(str(invoice_id))
        return dict(invoice) if invoice else None

    def _supplier(self, supplier_id: Any, supplier_name: Any) -> dict[str, Any] | None:
        suppliers = self.fixtures.get("suppliers")
        if not isinstance(suppliers, list):
            suppliers = load_suppliers()
        for supplier in suppliers:
            if not isinstance(supplier, dict):
                continue
            if supplier_id and str(supplier.get("supplier_id")) == str(supplier_id):
                return dict(supplier)
            if supplier_name and str(supplier.get("name")) == str(supplier_name):
                return dict(supplier)
        return None


def _metadata_value(context: dict[str, Any], key: str) -> Any:
    raw_metadata = context.get("metadata")
    metadata: dict[str, Any] = (
        cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
    )
    return context.get(key) if context.get(key) is not None else metadata.get(key)


def _data_sources(nodes: list[TraversalNode]) -> list[str]:
    return sorted({str(node.source) for node in nodes if node.source})


def _with_provenance(
    source: str,
    payload: dict[str, Any] | None = None,
    *,
    label: str | None = None,
) -> dict[str, Any]:
    provenance = dict(PROVENANCE_BY_SOURCE.get(source, PROVENANCE_BY_SOURCE["unavailable"]))
    if label:
        provenance["provenance_label"] = label
    return {"source": source, **provenance, **dict(payload or {})}


def _with_decision_provenance(
    decision: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if _is_verified_decision(decision):
        return _with_provenance(
            "graph_store",
            {"verified": True, **dict(payload or {})},
            label="decision history · verified outcome",
        ) | {"provenance_tier": "learned"}
    return _with_provenance(
        "graph_store",
        {"verified": False, **dict(payload or {})},
        label="decision history · GraphStore read",
    )


def _with_similar_decisions_provenance(
    decisions: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    verified_count = sum(1 for decision in decisions if _is_verified_decision(_flatten(decision)))
    base = {
        "verified": verified_count == len(decisions) and len(decisions) > 0,
        "verified_count": verified_count,
        "unverified_count": max(len(decisions) - verified_count, 0),
        **dict(payload or {}),
    }
    if base["verified"]:
        return _with_provenance("graph_store", base, label="decision history · verified outcomes") | {
            "provenance_tier": "learned"
        }
    return _with_provenance("graph_store", base, label="decision history · GraphStore read")


def _is_verified_decision(decision: dict[str, Any]) -> bool:
    if decision.get("verified") is True:
        return True
    if decision.get("outcome_verified") is True:
        return True
    if decision.get("verified_outcome") is True:
        return True
    outcome = decision.get("outcome")
    if isinstance(outcome, dict):
        if outcome.get("verified") is True or outcome.get("outcome_verified") is True:
            return True
        if str(outcome.get("status") or "").lower() in {"verified", "confirmed"}:
            return True
    receipt = decision.get("receipt")
    if isinstance(receipt, dict) and receipt.get("verified") is True:
        return True
    return False


def _similarity_criteria(
    *,
    supplier_id: Any,
    category: str | None,
    decision_id: str | None,
) -> dict[str, Any]:
    return {
        "supplier_id": str(supplier_id) if supplier_id not in (None, "") else None,
        "category": str(category) if category not in (None, "") else None,
        "exclude_decision_id": str(decision_id) if decision_id not in (None, "") else None,
        "order_by": "created_at DESC",
    }


def _decision_timestamp(decision: dict[str, Any]) -> str:
    flat = _flatten(decision)
    return str(_first(flat, "created_at", "timestamp", "decision_timestamp") or "")


def _flatten(decision: dict[str, Any]) -> dict[str, Any]:
    raw_metadata = decision.get("metadata")
    metadata: dict[str, Any] = (
        cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
    )
    return {**metadata, **decision}


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
