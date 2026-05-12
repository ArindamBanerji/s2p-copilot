import importlib.util
import json
from pathlib import Path

from app.graph_contract import S2P_GRAPH_CONTRACT


REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_SCRIPT = REPO_ROOT / "scripts" / "seed_s2p_graph.py"


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_s2p_graph", SEED_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_contract_has_required_node_types():
    expected = {
        "Invoice",
        "Supplier",
        "PurchaseOrder",
        "GoodsReceipt",
        "Contract",
        "Commodity",
        "ProcessModel",
        "ProcessVariant",
        "Activity",
    }

    assert set(S2P_GRAPH_CONTRACT["node_types"]) == expected
    assert len(S2P_GRAPH_CONTRACT["node_types"]) == 9


def test_contract_has_required_edge_types():
    expected = {
        "INVOICED_BY",
        "REFERENCES",
        "MATCHED_TO",
        "COVERS",
        "SUPPLIES",
        "HAS_VARIANT",
        "HAS_ACTIVITY",
        "BOTTLENECK_AT",
        "INVOICE_PATTERN",
    }

    assert {edge["type"] for edge in S2P_GRAPH_CONTRACT["edge_types"]} == expected
    assert len(S2P_GRAPH_CONTRACT["edge_types"]) == 9


def test_invoice_node_has_key_and_properties():
    invoice = S2P_GRAPH_CONTRACT["node_types"]["Invoice"]

    assert invoice["key"] == "invoice_id"
    assert {"invoice_id", "supplier_id", "po_number", "amount", "category"}.issubset(
        invoice["properties"]
    )


def test_supplier_node_has_key_and_properties():
    supplier = S2P_GRAPH_CONTRACT["node_types"]["Supplier"]

    assert supplier["key"] == "supplier_id"
    assert {"supplier_id", "name", "exception_rate", "payment_terms"}.issubset(
        supplier["properties"]
    )


def test_process_model_node_exists():
    assert S2P_GRAPH_CONTRACT["node_types"]["ProcessModel"]["key"] == "model_id"


def test_process_variant_node_exists():
    assert S2P_GRAPH_CONTRACT["node_types"]["ProcessVariant"]["key"] == "variant_id"


def test_activity_node_exists():
    assert S2P_GRAPH_CONTRACT["node_types"]["Activity"]["key"] == "activity_id"


def test_external_node_types_declared():
    external = S2P_GRAPH_CONTRACT["external_node_types"]

    assert external["PipelineSystem"]["key"] == "system_id"


def _edge(edge_type):
    return next(edge for edge in S2P_GRAPH_CONTRACT["edge_types"] if edge["type"] == edge_type)


def test_invoiced_by_connects_invoice_to_supplier():
    assert _edge("INVOICED_BY") == {
        "type": "INVOICED_BY",
        "from": "Invoice",
        "to": "Supplier",
    }


def test_invoice_pattern_connects_supplier_to_activity():
    assert _edge("INVOICE_PATTERN") == {
        "type": "INVOICE_PATTERN",
        "from": "Supplier",
        "to": "Activity",
    }


def test_has_variant_connects_model_to_variant():
    assert _edge("HAS_VARIANT") == {
        "type": "HAS_VARIANT",
        "from": "ProcessModel",
        "to": "ProcessVariant",
    }


def test_seed_script_reads_fixtures():
    seed = _load_seed_module()
    invoices = seed.load_invoices(REPO_ROOT / "data" / "synthetic_invoices.json")
    suppliers = seed.load_suppliers(REPO_ROOT / "data" / "s2p_demo_suppliers.json")

    assert len(invoices) == 50
    assert len(suppliers) == 10


def test_seed_script_creates_expected_node_count():
    seed = _load_seed_module()
    invoices = seed.load_invoices(REPO_ROOT / "data" / "synthetic_invoices.json")
    suppliers = seed.load_suppliers(REPO_ROOT / "data" / "s2p_demo_suppliers.json")
    plan = seed.build_seed_plan(invoices, suppliers, {}, limit=3)

    labels = [node["label"] for node in plan["nodes"]]
    assert labels.count("Invoice") == 3
    assert labels.count("Supplier") == 10
    assert labels.count("PurchaseOrder") == 3
    assert labels.count("GoodsReceipt") == 3
    assert plan["summary"]["node_count"] == len(plan["nodes"])
    assert plan["summary"]["edge_count"] == len(plan["edges"])


def test_seed_plan_includes_process_nodes():
    seed = _load_seed_module()
    process_data = {
        "process_model": "Purchase-to-Pay",
        "variant": "Standard with Returns",
        "variant_frequency": 340,
        "total_cases": 1247,
        "source": "celonis_cache",
        "activities": [
            {
                "id": "match_invoice_to_gr",
                "name": "Match Invoice to GR",
                "avg_duration_hours": 42.0,
                "case_count": 798,
                "system": "billing_api",
                "status": "degraded",
                "bottleneck": True,
                "bottleneck_cause": "MATKL_V2",
            }
        ],
        "cross_graph_insights": [{"supplier": "Aster Industrial Chemicals"}],
    }
    suppliers = [{"supplier_id": "SUP-001", "name": "Aster Industrial Chemicals"}]
    plan = seed.build_seed_plan([], suppliers, process_data)

    labels = {node["label"] for node in plan["nodes"]}
    edges = {edge["type"] for edge in plan["edges"]}
    assert {"ProcessModel", "ProcessVariant", "Activity", "PipelineSystem"}.issubset(labels)
    assert {"HAS_VARIANT", "HAS_ACTIVITY", "BOTTLENECK_AT", "INVOICE_PATTERN"}.issubset(edges)


def test_seed_process_data_loader_uses_first_existing_path(tmp_path):
    seed = _load_seed_module()
    fixture = tmp_path / "celonis_process_data.json"
    fixture.write_text(json.dumps({"process_model": "Purchase-to-Pay"}), encoding="utf-8")

    assert seed.load_process_data([tmp_path / "missing.json", fixture]) == {
        "process_model": "Purchase-to-Pay"
    }


def test_seed_script_uses_age_client_s_helper():
    seed = _load_seed_module()

    class FakeAGEClient:
        def __init__(self):
            self.calls = []

        def _S(self, value):
            self.calls.append(value)
            return "'" + str(value) + "'"

    client = FakeAGEClient()
    props = seed._props_literal(
        client,
        {
            "supplier_id": "SUP-001",
            "name": "Aster Industrial Chemicals",
        },
    )

    assert "supplier_id: 'SUP-001'" in props
    assert "name: 'Aster Industrial Chemicals'" in props
    assert client.calls == ["SUP-001", "Aster Industrial Chemicals"]


def test_seed_script_fails_if_client_missing_s_helper():
    seed = _load_seed_module()

    class MissingSClient:
        pass

    try:
        seed._serialize(MissingSClient(), "SUP-001")
    except AttributeError:
        return
    raise AssertionError("_serialize must require AGEClient._S")
