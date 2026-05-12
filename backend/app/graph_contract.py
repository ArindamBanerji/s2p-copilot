"""S2P graph contract for AGE seeding and validation."""

S2P_GRAPH_CONTRACT = {
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
