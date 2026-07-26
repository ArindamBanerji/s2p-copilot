"""Deterministic S2P graph seed data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from app.domains.s2p.config import S2P_CATEGORIES, S2P_FACTORS
from copilot_sdk.config import GraphConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
WORKSPACE_ROOT = REPO_ROOT.parent
SDK_CELONIS_PATH = (
    WORKSPACE_ROOT
    / "copilot-sdk"
    / "apps"
    / "dataops"
    / "backend"
    / "data"
    / "celonis_process_data.json"
)
SEED_CLEAN_DELETE_CYPHER = (
    "MATCH (d:Decision) WHERE d.domain = 's2p' DETACH DELETE d"
)


FALLBACK_PROCESS_DATA: dict[str, Any] = {
    "process_model": "Purchase-to-Pay",
    "source": "deterministic_fallback",
    "activities": [
        {"id": "create_purchase_order", "name": "Create Purchase Order", "avg_duration_hours": 3.4, "case_count": 1200, "status": "ok"},
        {"id": "goods_receipt", "name": "Post Goods Receipt", "avg_duration_hours": 18.2, "case_count": 1100, "status": "warning"},
        {"id": "match_invoice_to_gr", "name": "Match Invoice to GR", "avg_duration_hours": 42.0, "case_count": 800, "status": "degraded", "bottleneck": True},
        {"id": "release_payment", "name": "Release Payment", "avg_duration_hours": 11.6, "case_count": 730, "status": "ok"},
    ],
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _load_invoices() -> list[dict[str, Any]]:
    data = _load_json(DATA_DIR / "synthetic_invoices.json", [])
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _load_suppliers() -> list[dict[str, Any]]:
    data = _load_json(DATA_DIR / "s2p_demo_suppliers.json", [])
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _load_process_data() -> dict[str, Any]:
    for path in (DATA_DIR / "celonis_process_data.json", SDK_CELONIS_PATH):
        data = _load_json(path, {})
        if isinstance(data, dict) and data.get("activities"):
            return data
    return FALLBACK_PROCESS_DATA


def _slug(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    cleaned = [ch if ch.isalnum() else "_" for ch in text]
    return "_".join("".join(cleaned).split("_")).strip("_") or "unknown"


def _node_id(label: str, natural_key: Any) -> str:
    return f"{label}:{_slug(natural_key)}"


def _add_node(
    nodes: list[dict[str, Any]],
    seen: dict[str, dict[str, Any]],
    label: str,
    natural_key: Any,
    properties: dict[str, Any],
) -> str:
    node_id = _node_id(label, natural_key)
    if node_id not in seen:
        node = {"id": node_id, "label": label, "properties": dict(properties)}
        nodes.append(node)
        seen[node_id] = node
    return node_id


def _add_edge(
    edges: list[dict[str, Any]],
    seen: set[tuple[str, str, str]],
    label: str,
    from_id: str,
    to_id: str,
    properties: dict[str, Any] | None = None,
) -> None:
    token = (label, from_id, to_id)
    if token in seen:
        return
    seen.add(token)
    edges.append(
        {
            "id": f"{label}:{len(edges) + 1}",
            "label": label,
            "from_id": from_id,
            "to_id": to_id,
            "properties": properties or {},
        }
    )


def _validate_seed_target(graph: str | None) -> str:
    """Require an explicit disposable graph for any operational seed call."""
    target = str(graph or "").strip()
    if not target:
        raise ValueError("--graph is required for S2P seeding")
    if target == "soc_graph" and os.environ.get("ALLOW_PRODUCTION_SEED") != "1":
        raise PermissionError("Refusing to seed soc_graph without ALLOW_PRODUCTION_SEED=1")
    return target


def seed_graph(*, graph: str | None = None, seed: int = 42) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build seed data only after validating a disposable graph target.

    GraphConfig supplies the shared S2P DSN/graph configuration; this helper
    deliberately returns data and performs no deletion or production write.
    Any operational writer must scope cleanup to ``d.domain = 's2p'``.
    """
    _validate_seed_target(graph)
    GraphConfig.load("s2p")
    return seed_s2p_graph(seed=seed)


def seed_s2p_graph(seed: int = 42) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _ = seed
    invoices = _load_invoices()
    suppliers = _load_suppliers()
    process_data = _load_process_data()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: dict[str, dict[str, Any]] = {}
    seen_edges: set[tuple[str, str, str]] = set()

    category_ids = {
        category: _add_node(nodes, seen_nodes, "Category", category, {"category_id": category, "name": category})
        for category in S2P_CATEGORIES
    }
    factor_ids = {
        factor: _add_node(nodes, seen_nodes, "Factor", factor, {"factor_id": factor, "name": factor})
        for factor in S2P_FACTORS
    }
    rule_ids = {
        category: _add_node(
            nodes,
            seen_nodes,
            "ComplianceRule",
            f"{category}_rule",
            {
                "rule_id": f"{category}_rule",
                "name": f"{category.replace('_', ' ').title()} review rule",
                "category": category,
            },
        )
        for category in S2P_CATEGORIES
    }

    supplier_ids = {
        str(supplier.get("supplier_id")): _add_node(
            nodes,
            seen_nodes,
            "Supplier",
            supplier.get("supplier_id"),
            {
                "supplier_id": supplier.get("supplier_id"),
                "name": supplier.get("name"),
                "category": supplier.get("category"),
                "exception_rate": supplier.get("exception_rate"),
                "payment_terms": supplier.get("payment_terms"),
                "otif_score": supplier.get("otif_score"),
            },
        )
        for supplier in suppliers
        if supplier.get("supplier_id")
    }
    first_invoice_node_id: str | None = None
    first_supplier_node_id: str | None = None

    for index, invoice in enumerate(invoices):
        invoice_id = str(invoice.get("invoice_id") or f"invoice-{index}")
        supplier_id = str(invoice.get("supplier_id") or "")
        po_number = str(invoice.get("po_number") or f"po-{invoice_id}")
        category = str(invoice.get("category") or S2P_CATEGORIES[index % len(S2P_CATEGORIES)])
        raw_factors = invoice.get("factors")
        factors: dict[str, Any] = (
            cast(dict[str, Any], raw_factors) if isinstance(raw_factors, dict) else {}
        )
        raw_metadata = invoice.get("metadata")
        metadata: dict[str, Any] = (
            cast(dict[str, Any], raw_metadata) if isinstance(raw_metadata, dict) else {}
        )

        decision_id = _add_node(
            nodes,
            seen_nodes,
            "Decision",
            invoice_id,
            {
                "decision_id": f"decision_{invoice_id}",
                "domain": "s2p",
                "invoice_id": invoice_id,
                "category": category,
                "recommended_action": invoice.get("ground_truth_action"),
                "confidence": max((float(value) for value in factors.values() if isinstance(value, (int, float))), default=0.0),
                "created_at": metadata.get("invoice_date"),
            },
        )
        invoice_node_id = _add_node(
            nodes,
            seen_nodes,
            "Invoice",
            invoice_id,
            {
                "invoice_id": invoice_id,
                "supplier_id": supplier_id,
                "po_number": po_number,
                "amount": invoice.get("amount"),
                "currency": invoice.get("currency"),
                "category": category,
                "ground_truth_action": invoice.get("ground_truth_action"),
            },
        )
        if first_invoice_node_id is None:
            first_invoice_node_id = invoice_node_id
        if first_supplier_node_id is None and supplier_id in supplier_ids:
            first_supplier_node_id = supplier_ids[supplier_id]
        po_id = _add_node(
            nodes,
            seen_nodes,
            "PurchaseOrder",
            po_number,
            {
                "po_id": po_number,
                "po_number": po_number,
                "supplier_id": supplier_id,
                "currency": invoice.get("currency"),
            },
        )

        _add_edge(edges, seen_edges, "DECIDED_ON", decision_id, invoice_node_id)
        if supplier_id in supplier_ids:
            _add_edge(edges, seen_edges, "SUPPLIED_BY", invoice_node_id, supplier_ids[supplier_id])
        _add_edge(edges, seen_edges, "MATCHED_TO", invoice_node_id, po_id)
        _add_edge(edges, seen_edges, "IN_CATEGORY", invoice_node_id, category_ids[category])
        _add_edge(edges, seen_edges, "VIOLATES", invoice_node_id, rule_ids[category])
        for factor in S2P_FACTORS:
            _add_edge(
                edges,
                seen_edges,
                "EVALUATED_WITH",
                decision_id,
                factor_ids[factor],
                {"value": factors.get(factor)},
            )

    if first_invoice_node_id is not None:
        commodity_index_id = _add_node(
            nodes,
            seen_nodes,
            "CommodityIndex",
            "commodity_index_demo",
            {
                "commodity": "demo",
                "delta_pct": 0.0,
                "lookback_days": 30,
                "as_of": "fixture",
            },
        )
        contract_clause_id = _add_node(
            nodes,
            seen_nodes,
            "ContractClause",
            "contract_clause_demo",
            {
                "ref": "demo",
                "threshold_pct": 0.0,
                "clause_type": "demo",
            },
        )
        goods_receipt_id = _add_node(
            nodes,
            seen_nodes,
            "GoodsReceipt",
            "goods_receipt_demo",
            {
                "gr_id": "goods_receipt_demo",
                "qty_received": 0,
                "date": "fixture",
            },
        )
        _add_edge(edges, seen_edges, "HAS_COMMODITY_INDEX", first_invoice_node_id, commodity_index_id)
        _add_edge(edges, seen_edges, "GOVERNED_BY", first_invoice_node_id, contract_clause_id)
        _add_edge(edges, seen_edges, "RECEIVED_AS", first_invoice_node_id, goods_receipt_id)

    if first_supplier_node_id is not None:
        compliance_history_id = _add_node(
            nodes,
            seen_nodes,
            "ComplianceHistory",
            "compliance_history_demo",
            {
                "rule_id": "compliance_history_demo",
                "pass_rate": 1.0,
                "sample_count": 0,
            },
        )
        _add_edge(edges, seen_edges, "COMPLIANCE_RECORD", first_supplier_node_id, compliance_history_id)

    model_id = _add_node(
        nodes,
        seen_nodes,
        "ProcessModel",
        process_data.get("process_model") or "Purchase-to-Pay",
        {
            "model_id": _slug(process_data.get("process_model") or "Purchase-to-Pay"),
            "name": process_data.get("process_model") or "Purchase-to-Pay",
            "source": process_data.get("source", "deterministic_fallback"),
        },
    )
    activity_ids: list[str] = []
    for activity in process_data.get("activities", []):
        if not isinstance(activity, dict):
            continue
        activity_id = _add_node(
            nodes,
            seen_nodes,
            "Activity",
            activity.get("id") or activity.get("name"),
            {
                "activity_id": activity.get("id") or _slug(activity.get("name")),
                "name": activity.get("name"),
                "avg_duration_hours": activity.get("avg_duration_hours"),
                "case_count": activity.get("case_count"),
                "status": activity.get("status"),
                "bottleneck": bool(activity.get("bottleneck")),
            },
        )
        activity_ids.append(activity_id)
        _add_edge(edges, seen_edges, "CONTAINS", model_id, activity_id)

    for left, right in zip(activity_ids, activity_ids[1:]):
        _add_edge(edges, seen_edges, "FOLLOWS", left, right)

    first_supplier_id = next(iter(supplier_ids.values()), None)
    for activity_id in activity_ids:
        node = seen_nodes[activity_id]
        if node["properties"].get("bottleneck") and first_supplier_id:
            _add_edge(edges, seen_edges, "BOTTLENECK_AT", activity_id, first_supplier_id)

    return nodes, edges
