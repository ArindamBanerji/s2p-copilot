"""Category-specific S2P situation traversal patterns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from copilot_sdk.situation import (
    ContextChain,
    SituationContext,
    TraversalEdge,
    TraversalNode,
    TypedIntent,
)

from app.domains.s2p.config import S2PDomainConfig
from app.routers.s2p_data_helpers import find_invoice
from app.services.s2p_evidence_templates import S2P_FACTOR_MAP, evidence_context_from_record
from app.services.s2p_situation_pattern import _intent_context
from app.services.situation_graph_enrichment import NAMESPACE as SITUATION_ENRICHMENT_NAMESPACE


SITUATION_NL_TEMPLATES: dict[str, str] = {
    "price_variance": (
        "{variance_pct}% price delta. {commodity} moved {commodity_delta}% in "
        "{lookback_days} days. Contract {ref} {allows_blocks} pass-through up to "
        "{threshold_pct}%. {within_exceeds} bounds. -> {action}. Confidence: {confidence_pct}."
    ),
    "quantity_mismatch": (
        "Invoice qty {inv_qty} vs PO {po_qty} (Delta {delta}). GR confirms {gr_qty} "
        "received. {match_status}. -> {action}."
    ),
    "duplicate_risk": (
        "Invoice {invoice_id} from {supplier}. Similar: {match_id} dated {match_date}, "
        "amount {match_amount} ({similarity_pct}% match). {verdict}. -> {action}."
    ),
    "contract_gap": (
        "PO {po_id}. Contract {ref} covers {scope}. {covered_pct}% covered. "
        "Gap: {gap_items}. -> {action}."
    ),
    "format_compliance": (
        "Invoice from {supplier} fails {n} format rules. Issues: {issues}. "
        "Historical compliance: {historical_pct}%. -> {action}."
    ),
}


@dataclass
class S2PTraversalPatternBase:
    domain: str = "s2p"
    name: str = "s2p_category_context"
    category: str = ""
    default_max_depth: int = 3

    def supports(self, intent: TypedIntent) -> bool:
        if intent.domain != self.domain:
            return False
        context = _intent_context(intent)
        category = str(
            context.get("category")
            or intent.scope.get("category")
            or intent.metadata.get("category")
            or ""
        )
        return category == self.category

    def traverse(
        self,
        intent: TypedIntent,
        *,
        graph_store: Any = None,
        max_depth: int = 3,
    ) -> SituationContext:
        depth = _bounded_depth(max_depth)
        prepared = _prepare_context(intent, graph_store, self.category)
        prepared.graph_context = _query_graph_context(graph_store, prepared.invoice_id, min(3, depth + 1))
        nodes, edges, warnings, available = self._traversal(prepared, depth, graph_store)
        variables = self._variables(prepared, graph_store)
        variables.setdefault("action", prepared.action)
        variables.setdefault("confidence", prepared.confidence)
        variables.setdefault("confidence_pct", _confidence_pct(prepared.confidence))
        variables.setdefault("category", self.category)
        factor_names = _factors_for_category(self.category)
        context = SituationContext(
            domain=self.domain,
            decision_id=prepared.decision_id,
            intent=intent,
            pattern_name=self.name,
            nodes=nodes,
            edges=edges,
            evidence_chain=[
                {
                    "type": "category_traversal",
                    "category": self.category,
                    "path": [node.type for node in nodes],
                    "provenance": _evidence_chain_provenance(nodes),
                }
            ],
            max_depth=depth,
            truncated=any(node.depth >= depth for node in nodes) and depth < 3,
            warnings=warnings,
            metadata={
                "category": self.category,
                "template_variables": variables,
                "confidence": prepared.confidence,
                "factors_used": factor_names,
                "factor_count": len(list(S2PDomainConfig.factors)),
                "context_available": available,
                "traversal_path": [node.type for node in nodes],
                "hop_count": max((node.depth for node in nodes), default=0),
            },
        )
        return context

    def _traversal(
        self,
        prepared: "_PreparedContext",
        max_depth: int,
        graph_store: Any,
    ) -> tuple[list[TraversalNode], list[TraversalEdge], list[str], bool]:
        raise NotImplementedError

    def _variables(self, prepared: "_PreparedContext", graph_store: Any) -> dict[str, Any]:
        return dict(prepared.variables)


@dataclass
class PriceVarianceTraversal(S2PTraversalPatternBase):
    name: str = "s2p_price_variance_context"
    category: str = "price_variance"

    def _traversal(self, prepared: "_PreparedContext", max_depth: int, graph_store: Any):
        nodes = [_invoice_node(prepared, 0)]
        edges: list[TraversalEdge] = []
        warnings: list[str] = []
        available = True
        commodity = _first(prepared.record, "commodity") or _first(prepared.variables, "commodity")
        if max_depth >= 1:
            if not _first(prepared.record, "commodity") or commodity == "unknown":
                commodity = "Commodity data unavailable"
                warnings.append("Commodity data unavailable")
                available = False
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "commodity_index", f"commodity:{commodity}", commodity, 1, {
                "commodity": commodity,
                "commodity_delta": prepared.variables.get("commodity_delta"),
                "lookback_days": prepared.variables.get("lookback_days"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[0], nodes[-1], "USES_COMMODITY_INDEX", 1))
        if max_depth >= 2:
            contract_ref = str(prepared.variables.get("ref") or "No contract clause found")
            if not _first(prepared.record, "contract_ref", "contract_id", "ref") or contract_ref == "unknown":
                contract_ref = "No contract clause found"
                warnings.append("No contract clause found")
                available = False
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "contract_clause", f"contract_clause:{contract_ref}", contract_ref, 2, {
                "ref": contract_ref,
                "allows_blocks": prepared.variables.get("allows_blocks"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "CHECKS_CONTRACT_CLAUSE", 2))
        if max_depth >= 3:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "threshold", f"threshold:{prepared.variables.get('threshold_pct')}", "Threshold", 3, {
                "threshold_pct": prepared.variables.get("threshold_pct"),
                "within_bounds": prepared.variables.get("within_bounds"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "APPLIES_THRESHOLD", 3))
        return nodes, edges, warnings, available


@dataclass
class QuantityMismatchTraversal(S2PTraversalPatternBase):
    name: str = "s2p_quantity_mismatch_context"
    category: str = "quantity_mismatch"

    def _traversal(self, prepared: "_PreparedContext", max_depth: int, graph_store: Any):
        nodes = [_invoice_node(prepared, 0)]
        edges: list[TraversalEdge] = []
        warnings: list[str] = []
        available = True
        if max_depth >= 1:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "purchase_order", str(prepared.variables.get("po_id")), "Purchase Order", 1, {
                "po_id": prepared.variables.get("po_id"),
                "po_qty": prepared.variables.get("po_qty"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[0], nodes[-1], "MATCHES_PO", 1))
        if max_depth >= 2:
            gr_qty = prepared.variables.get("gr_qty")
            if not _first(prepared.record, "gr_qty", "goods_receipt_qty", "received_qty"):
                gr_qty = "GR data pending"
                warnings.append("GR data pending")
                available = False
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "goods_receipt", f"gr:{prepared.variables.get('po_id')}", "Goods Receipt", 2, {
                "gr_qty": gr_qty,
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "CONFIRMED_BY_GR", 2))
        if max_depth >= 3:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "delta", f"delta:{prepared.invoice_id}", "Quantity Delta", 3, {
                "delta": prepared.variables.get("delta"),
                "match_status": prepared.variables.get("match_status"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "COMPUTES_DELTA", 3))
        return nodes, edges, warnings, available


@dataclass
class DuplicateRiskTraversal(S2PTraversalPatternBase):
    name: str = "s2p_duplicate_risk_context"
    category: str = "duplicate_risk"

    def _traversal(self, prepared: "_PreparedContext", max_depth: int, graph_store: Any):
        nodes = [_invoice_node(prepared, 0)]
        edges: list[TraversalEdge] = []
        similar = prepared.similar_decision or _similar_decision(graph_store, prepared)
        if similar:
            prepared.similar_decision = similar
            _apply_similar_variables(prepared.variables, similar)
        if max_depth >= 1:
            nodes.append(_node(str(prepared.variables.get("match_id")), "similar_invoice", "Similar Invoice", 1, {
                "match_id": prepared.variables.get("match_id"),
                "match_amount": prepared.variables.get("match_amount"),
                "similarity_pct": prepared.variables.get("similarity_pct"),
                "provenance": "graph_store" if prepared.similar_decision else "fixture",
            }))
            edges.append(_edge(nodes[0], nodes[-1], "SIMILAR_TO", 1))
        if max_depth >= 2:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "supplier", str(prepared.variables.get("supplier")), str(prepared.variables.get("supplier")), 2, {
                "supplier": prepared.variables.get("supplier"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "FROM_SUPPLIER", 2))
        if max_depth >= 3:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "amount_match", f"amount:{prepared.variables.get('match_amount')}", "Amount Match", 3, {
                "match_amount": prepared.variables.get("match_amount"),
                "verdict": prepared.variables.get("verdict"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "COMPARES_AMOUNT", 3))
        return nodes, edges, [], True

    def _variables(self, prepared: "_PreparedContext", graph_store: Any) -> dict[str, Any]:
        variables = dict(prepared.variables)
        similar = prepared.similar_decision or _similar_decision(graph_store, prepared)
        if similar:
            _apply_similar_variables(variables, similar)
            prepared.similar_decision = similar
        return variables


@dataclass
class ContractGapTraversal(S2PTraversalPatternBase):
    name: str = "s2p_contract_gap_context"
    category: str = "contract_gap"

    def _traversal(self, prepared: "_PreparedContext", max_depth: int, graph_store: Any):
        nodes = [_invoice_node(prepared, 0)]
        edges: list[TraversalEdge] = []
        warnings: list[str] = []
        available = True
        if max_depth >= 1:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "purchase_order", str(prepared.variables.get("po_id")), "Purchase Order", 1, {
                "po_id": prepared.variables.get("po_id"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[0], nodes[-1], "MATCHES_PO", 1))
        if max_depth >= 2:
            ref = str(prepared.variables.get("ref") or "No contract clause found")
            if not _first(prepared.record, "contract_ref", "contract_id", "ref") or ref == "unknown":
                ref = "No contract clause found"
                warnings.append("No contract clause found")
                available = False
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "contract", f"contract:{ref}", ref, 2, {
                "ref": ref,
                "scope": prepared.variables.get("scope"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "REFERENCES_CONTRACT", 2))
        if max_depth >= 3:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "coverage", f"coverage:{prepared.invoice_id}", "Coverage Analysis", 3, {
                "covered_pct": prepared.variables.get("covered_pct"),
                "gap_items": prepared.variables.get("gap_items"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "ANALYZES_COVERAGE", 3))
        return nodes, edges, warnings, available


@dataclass
class FormatComplianceTraversal(S2PTraversalPatternBase):
    name: str = "s2p_format_compliance_context"
    category: str = "format_compliance"

    def _traversal(self, prepared: "_PreparedContext", max_depth: int, graph_store: Any):
        nodes = [_invoice_node(prepared, 0)]
        edges: list[TraversalEdge] = []
        if max_depth >= 1:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "rules", f"rules:{prepared.invoice_id}", "Format Rules", 1, {
                "fail_count": prepared.variables.get("n"),
                "issues": prepared.variables.get("issues"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[0], nodes[-1], "CHECKS_RULES", 1))
        if max_depth >= 2:
            nodes.append(_graph_or_fixture_node(prepared, graph_store, "historical_compliance", f"history:{prepared.variables.get('supplier')}", "Historical Compliance", 2, {
                "supplier": prepared.variables.get("supplier"),
                "historical_pct": prepared.variables.get("historical_pct"),
                "provenance": "fixture",
            }))
            edges.append(_edge(nodes[-2], nodes[-1], "USES_HISTORY", 2))
        return nodes, edges, [], True


S2P_TRAVERSAL_PATTERNS = [
    PriceVarianceTraversal(),
    QuantityMismatchTraversal(),
    DuplicateRiskTraversal(),
    ContractGapTraversal(),
    FormatComplianceTraversal(),
]


def pattern_for_category(category: str) -> S2PTraversalPatternBase | None:
    for pattern in S2P_TRAVERSAL_PATTERNS:
        if pattern.category == category:
            return pattern
    return None


def build_context_chain(context: SituationContext, nl_explanation: str | None = None) -> ContextChain:
    variables = context.metadata.get("template_variables")
    confidence = context.metadata.get("confidence")
    return ContextChain(
        context=context,
        traversal_path=[node.type for node in context.nodes],
        hop_count=max((node.depth for node in context.nodes), default=0),
        confidence=_float(confidence, 0.0),
        nl_explanation=nl_explanation,
        template_variables=dict(variables) if isinstance(variables, dict) else {},
    )


@dataclass
class _PreparedContext:
    decision_id: str | None
    invoice_id: str
    decision: dict[str, Any] | None
    invoice: dict[str, Any] | None
    record: dict[str, Any]
    variables: dict[str, Any]
    action: str
    confidence: float
    similar_decision: dict[str, Any] | None = None
    graph_context: list[dict[str, Any]] | None = None


def _prepare_context(intent: TypedIntent, graph_store: Any, category: str) -> _PreparedContext:
    intent_context = _intent_context(intent)
    decision_id = str(_first(intent_context, "decision_id") or intent.decision_id or "") or None
    decision = _get_decision(graph_store, decision_id)
    flat_decision = _flatten(decision or {})
    invoice_id = str(
        _first(intent_context, "invoice_id", "event_id", "source_invoice_id", "source_event_id")
        or _first(flat_decision, "invoice_id", "source_invoice_id", "entity_id")
        or decision_id
        or "unknown"
    )
    invoice = find_invoice(invoice_id) if invoice_id != "unknown" else None
    record: dict[str, Any] = {}
    if invoice:
        record.update(invoice)
    if decision:
        record.update(flat_decision)
        _remove_fixture_overrides_missing_from_decision(record, flat_decision)
    record.update({key: value for key, value in intent_context.items() if value is not None})
    record["category"] = category
    if isinstance(flat_decision.get("factors"), dict):
        record["factors"] = dict(cast(dict[str, Any], flat_decision["factors"]))
    variables = evidence_context_from_record(record)
    _add_aliases(variables)
    action = str(
        _first(record, "recommended_action", "action", "ground_truth_action")
        or variables.get("action")
        or "unknown"
    )
    confidence = _float(_first(record, "confidence"), 0.0)
    return _PreparedContext(
        decision_id=decision_id,
        invoice_id=invoice_id,
        decision=decision,
        invoice=invoice,
        record=record,
        variables=variables,
        action=action,
        confidence=confidence,
    )


def _add_aliases(variables: dict[str, Any]) -> None:
    variables["lookback_days"] = variables.get("lookback_days") or variables.get("lookback") or 30
    variables["threshold_pct"] = variables.get("threshold_pct") or variables.get("threshold") or 20.0
    variables["within_bounds"] = str(variables.get("within_exceeds") or "within") == "within"
    variables["ref"] = variables.get("ref") or variables.get("contract_ref") or "unknown"
    variables["match_amount"] = variables.get("match_amount") or variables.get("match_amt") or 0.0
    variables["similarity_pct"] = variables.get("similarity_pct") or variables.get("similarity") or 0.0
    variables["n"] = variables.get("n") or variables.get("n_rules") or 0.0
    variables["issues"] = variables.get("issues_list") or variables.get("issues") or "none"
    variables["historical_pct"] = variables.get("historical_pct") or variables.get("compliance_pct") or 0.0


def _query_graph_context(
    graph_store: Any,
    entity_id: str,
    max_depth: int,
) -> list[dict[str, Any]]:
    query_context = getattr(graph_store, "query_context", None)
    if not callable(query_context):
        return []
    try:
        rows = query_context(str(entity_id), max_depth)
    except Exception:
        return []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _graph_or_fixture_node(
    prepared: _PreparedContext,
    graph_store: Any,
    node_type: str,
    node_id: str,
    label: str,
    depth: int,
    properties: dict[str, Any],
) -> TraversalNode:
    graph_row = _find_graph_row(prepared.graph_context or [], node_type)
    if graph_row is None:
        return _node(node_id, node_type, label, depth, properties)
    graph_properties = _graph_properties(graph_row)
    enriched_properties = _read_enriched_properties(graph_store, node_type, str(graph_row.get("id") or graph_properties.get("entity_id") or ""))
    provenance = "enriched" if enriched_properties else "graph_store"
    merged = {**dict(properties), **graph_properties, **enriched_properties, "provenance": provenance}
    return _node(
        str(graph_row.get("id") or graph_properties.get("id") or node_id),
        node_type,
        str(graph_properties.get("label") or label),
        depth,
        merged,
    )


def _find_graph_row(rows: list[dict[str, Any]], node_type: str) -> dict[str, Any] | None:
    aliases = _node_aliases(node_type)
    for row in rows:
        node = row.get("node")
        properties = _graph_properties(row)
        edge_type = str(properties.get("edge_type") or "")
        row_id = str(row.get("id") or properties.get("entity_id") or "")
        candidates = [
            node if isinstance(node, str) else None,
            properties.get("type"),
            properties.get("node_type"),
            properties.get("_label"),
            properties.get("label"),
            _node_type_from_edge(edge_type),
            _node_type_from_entity_id(row_id),
        ]
        if any(_normalize_node_type(candidate) in aliases for candidate in candidates):
            return row
    return None


def _node_aliases(node_type: str) -> set[str]:
    canonical = {
        "commodity_index": "CommodityIndex",
        "contract": "ContractClause",
        "contract_clause": "ContractClause",
        "goods_receipt": "GoodsReceipt",
        "compliance_rule": "ComplianceHistory",
        "historical_compliance": "ComplianceHistory",
    }.get(node_type, node_type)
    return {
        _normalize_node_type(node_type),
        _normalize_node_type(canonical),
        _normalize_node_type(node_type.replace("_", "")),
        _normalize_node_type(node_type.replace("_", "-")),
    }


def _normalize_node_type(value: Any) -> str:
    return str(value or "").replace("_", "").replace("-", "").lower()


def _node_type_from_edge(edge_type: str) -> str | None:
    return {
        "HAS_COMMODITY_INDEX": "commodity_index",
        "GOVERNED_BY": "contract_clause",
        "RECEIVED_AS": "goods_receipt",
        "COMPLIANCE_RECORD": "historical_compliance",
    }.get(edge_type)


def _node_type_from_entity_id(entity_id: str) -> str | None:
    prefix = entity_id.split(":", 1)[0]
    return {
        "CommodityIndex": "commodity_index",
        "ContractClause": "contract_clause",
        "GoodsReceipt": "goods_receipt",
        "ComplianceHistory": "historical_compliance",
    }.get(prefix)


def _read_enriched_properties(graph_store: Any, node_type: str, entity_id: str) -> dict[str, Any]:
    entity_type = {
        "commodity_index": "CommodityIndex",
        "contract": "ContractClause",
        "contract_clause": "ContractClause",
        "goods_receipt": "GoodsReceipt",
        "compliance_rule": "ComplianceHistory",
        "historical_compliance": "ComplianceHistory",
    }.get(node_type)
    reader = getattr(graph_store, "read_entity_enrichment", None)
    if not entity_type or not entity_id or not callable(reader):
        return {}
    try:
        metrics = reader(
            domain="s2p",
            entity_type=entity_type,
            entity_id=entity_id,
            namespace=SITUATION_ENRICHMENT_NAMESPACE,
        )
    except Exception:
        return {}
    if not isinstance(metrics, dict):
        return {}
    return {
        str(name): getattr(value, "value", value)
        for name, value in metrics.items()
    }


def _graph_properties(row: dict[str, Any]) -> dict[str, Any]:
    node = row.get("node")
    if isinstance(node, dict):
        base = dict(node)
    else:
        base = {}
    properties = row.get("properties")
    if isinstance(properties, dict):
        base.update(properties)
    return base


def _apply_similar_variables(variables: dict[str, Any], similar: dict[str, Any]) -> None:
    flat = _flatten(similar)
    variables["match_id"] = _first(flat, "invoice_id", "entity_id", "decision_id") or variables.get("match_id")
    variables["match_date"] = _first(flat, "invoice_date", "created_at") or variables.get("match_date")
    variables["match_amount"] = _first(flat, "amount", "match_amount") or variables.get("match_amount")


def _remove_fixture_overrides_missing_from_decision(
    record: dict[str, Any],
    flat_decision: dict[str, Any],
) -> None:
    for key in ("commodity", "contract_ref", "contract_id", "ref", "gr_qty"):
        if key not in flat_decision:
            record.pop(key, None)


def _invoice_node(prepared: _PreparedContext, depth: int) -> TraversalNode:
    return _node(prepared.invoice_id, "invoice", "Invoice", depth, {
        "invoice_id": prepared.invoice_id,
        "amount": prepared.record.get("amount"),
        "category": prepared.record.get("category"),
        "supplier": _first(prepared.record, "supplier_name", "supplier_id", "supplier"),
        "provenance": _first(prepared.record, "provenance", "source") or (
            "learned" if prepared.decision else "fixture"
        ),
    })


def _node(node_id: str, node_type: str, label: str, depth: int, properties: dict[str, Any]) -> TraversalNode:
    clean = {key: value for key, value in dict(properties).items() if value is not None}
    clean.setdefault("provenance", "fixture")
    return TraversalNode(
        id=str(node_id or f"{node_type}:unknown"),
        type=node_type,
        label=str(label or node_type),
        properties=clean,
        depth=depth,
        source=str(clean.get("provenance") or "fixture"),
    )


def _evidence_chain_provenance(nodes: list[TraversalNode]) -> str:
    order = {
        "fixture": 0,
        "sample": 0,
        "demo": 0,
        "synthetic": 0,
        "graph_store": 1,
        "enriched": 1,
        "context": 1,
        "external": 1,
        "feed": 1,
        "verified": 2,
        "proven": 2,
        "learned": 3,
        "decision": 3,
        "centroid": 3,
    }
    sources = [
        str(node.properties.get("provenance") or node.source or "fixture")
        for node in nodes
    ]
    if not sources:
        return "fixture"
    return min(sources, key=lambda source: order.get(source.lower(), 0))


def _edge(source: TraversalNode, target: TraversalNode, edge_type: str, depth: int) -> TraversalEdge:
    return TraversalEdge(
        source_id=source.id,
        target_id=target.id,
        type=edge_type,
        properties={"provenance": "fixture"},
        depth=depth,
    )


def _get_decision(graph_store: Any, decision_id: str | None) -> dict[str, Any] | None:
    get_decision = getattr(graph_store, "get_decision", None)
    if not decision_id or not callable(get_decision):
        return None
    try:
        decision = get_decision(decision_id)
    except Exception:
        return None
    return decision if isinstance(decision, dict) else None


def _similar_decision(graph_store: Any, prepared: _PreparedContext) -> dict[str, Any] | None:
    query_similar = getattr(graph_store, "query_similar", None)
    if callable(query_similar):
        try:
            rows = query_similar(prepared.decision_id or prepared.invoice_id, 1)
        except Exception:
            rows = []
        if rows and isinstance(rows[0], dict):
            return rows[0]
    return None


def _factors_for_category(category: str) -> list[str]:
    allowed = set(S2PDomainConfig.factors)
    return [factor for factor in S2P_FACTOR_MAP.get(category, []) if factor in allowed]


def _bounded_depth(value: Any) -> int:
    try:
        depth = int(value)
    except (TypeError, ValueError):
        depth = 3
    return max(0, min(depth, 3))


def _confidence_pct(value: Any) -> str:
    return f"{round(max(0.0, min(_float(value, 0.0), 1.0)) * 100.0):.0f}%"


def _flatten(decision: dict[str, Any]) -> dict[str, Any]:
    metadata = decision.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    return {**metadata_dict, **decision}


def _first(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
