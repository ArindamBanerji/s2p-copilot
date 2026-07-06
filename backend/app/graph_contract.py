"""S2P graph contract for graph seeding and validation."""

from __future__ import annotations

from typing import Any

from copilot_sdk.graph.contract import EdgeType, GraphContract, NodeType


LEGACY_GRAPH_CONTRACT: dict[str, Any] = {
    "graph_name": "s2p_graph",
    "external_node_types": {
        "PipelineSystem": {
            "key": "system_id",
            "properties": ["system_id", "name", "source"],
        }
    },
    "node_types": {
        "Invoice": {
            "key": "invoice_id",
            "properties": [
                "invoice_id",
                "supplier_id",
                "po_number",
                "amount",
                "currency",
                "category",
                "ground_truth_action",
            ],
        },
        "Supplier": {
            "key": "supplier_id",
            "properties": [
                "supplier_id",
                "name",
                "category",
                "exception_rate",
                "payment_terms",
                "otif_score",
            ],
        },
        "PurchaseOrder": {
            "key": "po_id",
            "properties": ["po_id", "po_number", "supplier_id", "currency"],
        },
        "GoodsReceipt": {
            "key": "gr_id",
            "properties": ["gr_id", "po_id", "invoice_id", "source"],
        },
        "Contract": {
            "key": "contract_id",
            "properties": ["contract_id", "supplier_id", "commodity_id"],
        },
        "Commodity": {
            "key": "commodity_id",
            "properties": ["commodity_id", "name"],
        },
        "ProcessModel": {
            "key": "model_id",
            "properties": ["model_id", "name", "source"],
        },
        "ProcessVariant": {
            "key": "variant_id",
            "properties": [
                "variant_id",
                "name",
                "variant_frequency",
                "total_cases",
            ],
        },
        "Activity": {
            "key": "activity_id",
            "properties": [
                "activity_id",
                "name",
                "avg_duration_hours",
                "case_count",
                "status",
                "bottleneck",
                "bottleneck_cause",
                "system",
            ],
        },
    },
    "edge_types": [
        {"type": "INVOICED_BY", "from": "Invoice", "to": "Supplier"},
        {"type": "REFERENCES", "from": "Invoice", "to": "PurchaseOrder"},
        {"type": "MATCHED_TO", "from": "Invoice", "to": "GoodsReceipt"},
        {"type": "COVERS", "from": "Contract", "to": "Commodity"},
        {"type": "SUPPLIES", "from": "Supplier", "to": "Commodity"},
        {"type": "HAS_VARIANT", "from": "ProcessModel", "to": "ProcessVariant"},
        {"type": "HAS_ACTIVITY", "from": "ProcessVariant", "to": "Activity"},
        {"type": "BOTTLENECK_AT", "from": "Activity", "to": "PipelineSystem"},
        {"type": "INVOICE_PATTERN", "from": "Supplier", "to": "Activity"},
    ],
}


class S2PGraphContract(GraphContract):
    """SDK contract with read-only legacy dictionary access."""

    def __getitem__(self, key: str) -> Any:
        if key == "graph_name":
            return self.graph_name
        return LEGACY_GRAPH_CONTRACT[key]


S2P_GRAPH_CONTRACT = S2PGraphContract(
    graph_name="s2p_graph",
    expected_nodes=187,
    expected_edges=662,
    node_types=[
        NodeType("Decision", ["decision_id", "invoice_id", "category", "recommended_action", "confidence", "created_at"]),
        NodeType("Invoice", ["invoice_id", "supplier_id", "po_number", "amount", "currency", "category", "ground_truth_action"]),
        NodeType("Supplier", ["supplier_id", "name", "category", "exception_rate", "payment_terms", "otif_score"]),
        NodeType("PurchaseOrder", ["po_id", "po_number", "supplier_id", "currency"]),
        NodeType("ProcessModel", ["model_id", "name", "source"]),
        NodeType("Activity", ["activity_id", "name", "avg_duration_hours", "case_count", "status", "bottleneck"]),
        NodeType("Category", ["category_id", "name"]),
        NodeType("Factor", ["factor_id", "name"]),
        NodeType("ComplianceRule", ["rule_id", "name", "category"]),
        NodeType("CommodityIndex", ["commodity", "delta_pct", "lookback_days", "as_of"]),
        NodeType("ContractClause", ["ref", "threshold_pct", "clause_type"]),
        NodeType("GoodsReceipt", ["gr_id", "qty_received", "date"]),
        NodeType("ComplianceHistory", ["rule_id", "pass_rate", "sample_count"]),
    ],
    edge_types=[
        EdgeType("DECIDED_ON", "Decision", "Invoice"),
        EdgeType("SUPPLIED_BY", "Invoice", "Supplier"),
        EdgeType("MATCHED_TO", "Invoice", "PurchaseOrder"),
        EdgeType("CONTAINS", "ProcessModel", "Activity"),
        EdgeType("FOLLOWS", "Activity", "Activity"),
        EdgeType("IN_CATEGORY", "Invoice", "Category"),
        EdgeType("EVALUATED_WITH", "Decision", "Factor"),
        EdgeType("VIOLATES", "Invoice", "ComplianceRule"),
        EdgeType("BOTTLENECK_AT", "Activity", "Supplier"),
        EdgeType("HAS_COMMODITY_INDEX", "Invoice", "CommodityIndex"),
        EdgeType("GOVERNED_BY", "Invoice", "ContractClause"),
        EdgeType("RECEIVED_AS", "Invoice", "GoodsReceipt"),
        EdgeType("COMPLIANCE_RECORD", "Supplier", "ComplianceHistory"),
    ],
)
